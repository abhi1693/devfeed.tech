import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from devfeed_core import cache, cache_events
from devfeed_core.config import get_settings
from devfeed_core.models import Article, IngestionJob, Source, Tag, utcnow
from redis.exceptions import ConnectionError
from sqlalchemy import delete, insert, update
from sqlalchemy.orm import make_transient_to_detached


class MemoryRedis:
    """Deterministic Redis stand-in; Lua's actual behavior has opt-in integration coverage."""

    def __init__(self):
        self.values = {}
        self.expires = {}
        self.now = 0
        self.mutex = threading.RLock()

    def get(self, key):
        with self.mutex:
            if key in self.expires and self.now >= self.expires[key]:
                self.values.pop(key, None)
            return self.values.get(key)

    def set(self, key, value, *, nx=False, ex=None):
        with self.mutex:
            if nx and self.get(key) is not None:
                return False
            self.values[key] = value if isinstance(value, bytes) else str(value).encode()
            if ex is not None:
                self.expires[key] = self.now + int(ex)
            else:
                self.expires.pop(key, None)
            return True

    def eval(self, script, count, *args):
        with self.mutex:
            if script == cache.RELEASE:
                key, token = args
                if self.get(key) == token.encode():
                    self.values.pop(key, None)
                    return 1
            elif script == cache.PUBLISH:
                epoch_key, lock_key, key, epoch, token, body, ttl = args
                if self.get(epoch_key) == epoch and self.get(lock_key) == token.encode():
                    self.set(key, body, ex=ttl)
                    return 1
            else:
                pytest.fail("Unexpected Redis script")
            return 0


@pytest.fixture
def response_cache(monkeypatch):
    from devfeed_api import cache as api_cache

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    store = cache.ResponseCache(MemoryRedis(), "test:cache")
    monkeypatch.setattr(cache, "get_cache", lambda: store)
    monkeypatch.setattr(api_cache, "get_cache", lambda: store)
    return store


@pytest.fixture
def cached_client(response_cache, monkeypatch):
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    records, calls = [], []

    def scalars(statement):
        calls.append(statement)
        return SimpleNamespace(all=lambda: records.copy())

    session = SimpleNamespace(scalars=scalars, get=lambda *args: None)
    dependencies = []

    def dependency():
        dependencies.append(True)
        yield session

    monkeypatch.setattr(get_settings(), "cors_origins", ["https://reader.example"])
    app = create_app()
    app.dependency_overrides[get_session] = dependency
    with TestClient(app) as client:
        yield client, session, calls, dependencies, records


def test_cached_hit_skips_database_dependency_and_keeps_request_headers_fresh(cached_client):
    client, _, calls, dependencies, _ = cached_client
    first = client.get("/v1/feed", headers={"Origin": "https://reader.example"})
    second = client.get("/v1/feed")
    assert first.json() == second.json() == {"items": [], "next_cursor": None}
    assert first.headers["x-cache"] == "MISS" and second.headers["x-cache"] == "HIT"
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert first.headers["access-control-allow-origin"] == "https://reader.example"
    assert "access-control-allow-origin" not in second.headers
    assert second.headers["cache-control"] == "no-store"
    assert len(calls) == len(dependencies) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/v1/sources",
        "/v1/tags",
        "/v1/topics",
    ],
)
def test_read_lists_are_cached(path, cached_client):
    client, _, calls, _, _ = cached_client
    assert client.get(path).headers["x-cache"] == "MISS"
    assert client.get(path).headers["x-cache"] == "HIT"
    assert len(calls) == 1


def test_query_order_is_normalized_without_losing_repeated_parameter_semantics(cached_client):
    client, _, calls, _, _ = cached_client
    assert client.get("/v1/feed?tag=python&limit=1&tag=fastapi").headers["x-cache"] == "MISS"
    assert client.get("/v1/feed?limit=1&tag=python&tag=fastapi").headers["x-cache"] == "HIT"
    for query in ("limit=2", "limit=1&limit=2", "limit=2&limit=1", "exclude_tag=python"):
        assert client.get(f"/v1/feed?{query}").headers["x-cache"] == "MISS"
    assert len(calls) == 5


def test_invalid_input_and_not_found_are_never_cached(cached_client, response_cache):
    client, _, _, _, _ = cached_client
    paths = ["/v1/feed?limit=0", "/v1/feed?cursor=invalid", f"/v1/articles/{uuid.uuid4()}"]
    for path in paths:
        assert client.get(path).status_code in (404, 422)
        assert client.get(path).status_code in (404, 422)
    assert all(key.endswith(":generation") for key in response_cache.redis.values)


@pytest.mark.parametrize(
    "headers,reason",
    [
        ({"Authorization": "Bearer private"}, "authorization"),
        ({"Cache-Control": "no-cache"}, "request_cache_control"),
        ({"Cache-Control": "no-store"}, "request_cache_control"),
    ],
)
def test_private_requests_and_explicit_bypass_do_not_read_or_fill_cache(
    headers, reason, cached_client
):
    client, _, calls, _, _ = cached_client
    for _ in range(2):
        result = client.get("/v1/feed", headers=headers)
        assert result.headers["x-cache"] == "BYPASS"
        assert result.headers["x-cache-bypass-reason"] == reason
    assert client.get("/v1/feed").headers["x-cache"] == "MISS"
    assert len(calls) == 3


@pytest.mark.parametrize("first_headers", [{}, {"Cookie": "unrelated_browser_state=one"}])
def test_nonpersonalized_reads_share_cache_despite_incidental_cookies(cached_client, first_headers):
    client, _, calls, dependencies, _ = cached_client
    first = client.get("/v1/feed", headers=first_headers)
    assert first.headers["x-cache"] == "MISS"
    for headers in ({"Cookie": "unrelated_browser_state=two"}, {}):
        result = client.get("/v1/feed", headers=headers)
        assert result.headers["x-cache"] == "HIT" and result.json() == first.json()
        assert "x-cache-bypass-reason" not in result.headers
    assert len(calls) == len(dependencies) == 1


def test_long_query_reports_bypass_reason(cached_client):
    client, _, _, _, _ = cached_client
    response = client.get("/v1/feed?unused=" + "x" * 4096)
    assert response.headers["x-cache"] == "BYPASS"
    assert response.headers["x-cache-bypass-reason"] == "query_too_long"


def test_health_docs_and_removed_public_writes_bypass_cache(
    cached_client, response_cache, monkeypatch
):
    client, _, _, _, _ = cached_client
    monkeypatch.setattr(response_cache, "lookup", lambda *a: pytest.fail("Used GET cache"))
    for _ in range(2):
        assert client.post("/v1/tags", json={"name": "Python", "slug": "python"}).status_code == 405
    for path in ("/health/live", "/openapi.json", "/docs"):
        response = client.get(path)
        assert response.status_code == 200 and "x-cache" not in response.headers


def test_cache_expiry_and_public_invalidation(cached_client, response_cache):
    client, _, _, _, _ = cached_client
    assert client.get("/v1/feed").headers["x-cache"] == "MISS"
    assert client.get("/v1/feed").headers["x-cache"] == "HIT"
    response_cache.redis.now += get_settings().cache_ttl_seconds
    assert client.get("/v1/feed").headers["x-cache"] == "MISS"
    assert client.get("/v1/tags").headers["x-cache"] == "MISS"
    cache.invalidate_public_cache()
    assert client.get("/v1/feed").headers["x-cache"] == "MISS"
    assert client.get("/v1/tags").headers["x-cache"] == "MISS"


def test_operational_routes_are_not_exposed_or_cached_by_public_api(cached_client, response_cache):
    client, _, _, _, _ = cached_client
    for _ in range(2):
        response = client.get("/v1/ingestion/jobs")
        assert response.status_code == 404
        assert "x-cache" not in response.headers
    assert response_cache.redis.values == {}


def test_cache_outage_falls_back_and_uses_short_circuit(cached_client, response_cache, monkeypatch):
    client, _, calls, _, _ = cached_client
    attempts = []
    original_get = response_cache.redis.get

    def unavailable(*args):
        attempts.append(True)
        raise ConnectionError("redis://private:secret@server")

    monkeypatch.setattr(response_cache.redis, "get", unavailable)
    for _ in range(2):
        result = client.get("/v1/feed")
        assert result.headers["x-cache"] == "BYPASS"
        assert result.headers["x-cache-bypass-reason"] == "cache_unavailable"
    assert len(calls) == 2 and len(attempts) == 1
    monkeypatch.setattr(response_cache.redis, "get", original_get)
    response_cache.blocked_until = 0
    assert client.get("/v1/feed").headers["x-cache"] == "MISS"
    assert client.get("/v1/feed").headers["x-cache"] == "HIT"


def test_late_loader_cannot_restore_invalidated_data(response_cache):
    lookup = response_cache.lookup("/v1/feed", "public")
    assert lookup.token
    response_cache.invalidate()
    assert not response_cache.publish(lookup, b'{"old":true}', 30)
    assert response_cache.lookup("/v1/feed", "public").body is None
    response_cache.release(lookup)


def test_expired_loader_cannot_overwrite_or_unlock_new_owner(response_cache):
    old = response_cache.lookup("feed", "public")
    response_cache.redis.now += cache.LOCK_SECONDS
    new = response_cache.lookup("feed", "public")
    response_cache.release(old)
    assert response_cache.redis.get(new.lock_key) == new.token.encode()
    assert not response_cache.publish(old, b"[]", 30)
    assert response_cache.publish(new, b'["new"]', 30)


@pytest.mark.parametrize("value", [b"broken-json", b"[" * 10000, b"x" * 1_000_001])
def test_corrupt_and_oversized_cache_entries_are_misses(response_cache, value):
    lookup = response_cache.lookup("feed", "public")
    response_cache.release(lookup)
    response_cache.redis.set(lookup.key, value)
    next_lookup = response_cache.lookup("feed", "public")
    assert next_lookup.body is None and next_lookup.token


def test_generation_eviction_never_reuses_previous_cached_values(response_cache):
    old = response_cache.lookup("feed", "public")
    assert response_cache.publish(old, b'["old"]', 30)
    response_cache.redis.values.pop(old.generation_key)
    current = response_cache.lookup("feed", "public")
    assert current.generation != old.generation and current.body is None


def test_concurrent_misses_share_one_database_load(cached_client):
    client, session, calls, dependencies, _ = cached_client
    original = session.scalars

    def slow(statement):
        time.sleep(0.1)
        return original(statement)

    session.scalars = slow
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.get("/v1/feed"), range(8)))
    assert all(response.status_code == 200 for response in responses)
    assert sum(response.headers["x-cache"] == "MISS" for response in responses) == 1
    assert len(calls) == len(dependencies) == 1


def test_commit_invalidation_is_not_called_for_rollback_or_savepoint_commit(monkeypatch):
    calls = []
    monkeypatch.setattr(cache_events, "invalidate_public_cache", lambda: calls.append(True))
    with cache_events.AppSession() as session:
        with session.begin():
            with session.begin_nested():
                session.info[cache_events.DIRTY] = True
            assert not calls
        assert calls == [True]
        with session.begin():
            pass
        assert calls == [True]
        session.begin()
        session.info[cache_events.DIRTY] = True
        session.rollback()
        session.commit()
        assert calls == [True]


@pytest.mark.parametrize("field", ["description", "logo_url", "approval_status", "enabled"])
def test_source_public_changes_invalidate_but_polling_does_not(field):
    source = Source(id=uuid.uuid4(), approval_status="approved", enabled=True)
    make_transient_to_detached(source)
    with cache_events.AppSession() as session:
        session.add(source)
        source.last_success_at = utcnow()
        source.next_fetch_at = utcnow()
        cache_events.track_objects(session, None, None)
        assert cache_events.DIRTY not in session.info
        setattr(source, field, False if field == "enabled" else "Changed")
        cache_events.track_objects(session, None, None)
        assert session.info[cache_events.DIRTY]


def test_private_article_enrichment_does_not_clear_public_feed_cache():
    article = Article(id=uuid.uuid4(), publication_status="unpublished")
    make_transient_to_detached(article)
    with cache_events.AppSession() as session:
        session.add(article)
        article.ai_summary = "Private analysis output"
        cache_events.track_objects(session, None, None)
        assert cache_events.DIRTY not in session.info


def test_unpublish_invalidates_even_though_new_state_is_private():
    article = Article(id=uuid.uuid4(), publication_status="published")
    make_transient_to_detached(article)
    with cache_events.AppSession() as session:
        session.add(article)
        article.publication_status = "unpublished"
        cache_events.track_objects(session, None, None)
        assert session.info[cache_events.DIRTY]


@pytest.mark.parametrize(
    "statement,expected",
    [
        (insert(Article), True),
        (update(Article).values(image_url="https://example.com/img"), True),
        (delete(Tag), True),
        (update(IngestionJob).values(status="succeeded"), False),
    ],
)
@pytest.mark.parametrize("affected", [0, 1, -1])
def test_direct_worker_dml_marks_public_cache_only_after_affected_rows(
    statement, expected, affected
):
    with cache_events.AppSession() as session:
        options = {}
        cache_events.track_statements(
            SimpleNamespace(
                statement=statement,
                session=session,
                is_insert=statement.is_insert,
                is_update=statement.is_update,
                is_delete=statement.is_delete,
                update_execution_options=lambda **kw: options.update(kw),
            )
        )
        assert cache_events.DIRTY not in session.info
        cache_events.track_affected_rows(
            None,
            SimpleNamespace(rowcount=affected),
            None,
            None,
            SimpleNamespace(execution_options=options),
            False,
        )
        assert bool(session.info.get(cache_events.DIRTY)) is (expected and affected != 0)


def test_cache_invalidation_failure_does_not_fail_a_database_commit(response_cache, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("secret")

    monkeypatch.setattr(response_cache.redis, "set", unavailable)
    with cache_events.AppSession() as session, session.begin():
        session.info[cache_events.DIRTY] = True


def test_keys_do_not_contain_search_terms_or_urls(response_cache):
    response_cache.lookup("/v1/feed?q=private-search&source_id=private-uuid", "public")
    assert "private" not in json.dumps(list(response_cache.redis.values))


def test_cache_clear_is_scoped_and_does_not_touch_rq(
    cached_client, response_cache, monkeypatch, capsys
):
    from devfeed_cli import commands
    from devfeed_cli.main import run

    monkeypatch.setattr(commands, "get_cache", lambda: response_cache)
    client, _, _, _, _ = cached_client
    response_cache.redis.set("rq:queue:ingestion", b"queued-jobs")
    response_cache.redis.set("devfeed:admin:session:example", b"admin-session")
    for path in ("/v1/feed", "/v1/tags"):
        client.get(path)
        assert client.get(path).headers["x-cache"] == "HIT"
    assert run(["cache", "clear"]) == 0
    assert json.loads(capsys.readouterr().out)["cleared"]
    assert response_cache.redis.get("rq:queue:ingestion") == b"queued-jobs"
    assert response_cache.redis.get("devfeed:admin:session:example") == b"admin-session"
    for path in ("/v1/feed", "/v1/tags"):
        assert client.get(path).headers["x-cache"] == "MISS"


def test_disable_switch_never_contacts_redis(cached_client, monkeypatch, response_cache):
    client, _, calls, _, _ = cached_client
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    monkeypatch.setattr(response_cache, "lookup", lambda *a: pytest.fail("Cache contacted"))
    result = client.get("/v1/feed")
    assert result.headers["x-cache"] == "BYPASS"
    assert result.headers["x-cache-bypass-reason"] == "disabled"
    assert client.get("/v1/feed").headers["x-cache"] == "BYPASS"
    assert len(calls) == 2


def test_wait_timeout_falls_back_without_overwriting_the_owner(
    cached_client, response_cache, monkeypatch
):
    from devfeed_api import cache as api_cache

    client, _, calls, _, _ = cached_client
    owner = response_cache.lookup("/v1/feed?", "public")
    monkeypatch.setattr(api_cache, "WAIT_SECONDS", 0)
    response = client.get("/v1/feed")
    assert response.status_code == 200 and len(calls) == 1
    assert response_cache.redis.get(owner.lock_key) == owner.token.encode()
    assert response_cache.redis.get(owner.key) is None


def test_failed_cache_publication_keeps_successful_database_response(
    cached_client, response_cache, monkeypatch
):
    client, _, _, _, _ = cached_client
    monkeypatch.setattr(
        response_cache.redis, "eval", lambda *a: (_ for _ in ()).throw(ConnectionError())
    )
    result = client.get("/v1/feed")
    assert result.status_code == 200 and result.json() == {"items": [], "next_cursor": None}


def test_new_public_records_and_relationships_mark_invalidation():
    from devfeed_core.models import ArticleOrigin

    for obj in (
        Tag(name="tag", slug="tag"),
        Article(title="Example", publication_status="published"),
        Source(name="Example"),
        ArticleOrigin(article_id=uuid.uuid4(), source_id=uuid.uuid4()),
    ):
        with cache_events.AppSession() as session:
            session.add(obj)
            cache_events.track_objects(session, None, None)
            assert session.info[cache_events.DIRTY]


def test_oversized_response_is_not_stored(cached_client, response_cache, monkeypatch):
    client, _, calls, _, rows = cached_client
    monkeypatch.setattr(get_settings(), "cache_max_bytes", 1024)
    rows.extend(
        Tag(
            id=uuid.uuid4(),
            name="x" * 100,
            slug=str(i),
            aliases=[],
            auto_link_topic=True,
            topic_match_status="pending",
        )
        for i in range(20)
    )
    for _ in range(2):
        assert client.get("/v1/tags").headers["x-cache"] == "MISS"
    assert len(calls) == 2
    assert all(key.endswith(":generation") for key in response_cache.redis.values)
