"""Application cache behavior with explicitly supplied disposable PostgreSQL/Redis."""

import uuid

import pytest
from devfeed_aggregator import image_tasks, source_tasks, tasks
from devfeed_core import cache, services
from devfeed_core.article_processing_retry import request_processing_retry
from devfeed_core.cache_events import DIRTY, REASONS
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import Article, ArticleImageJob, Source, Tag, utcnow
from devfeed_core.schemas import SourceDecision
from devfeed_core.search_engine import SearchUnavailable
from devfeed_core.search_index import sync_batch
from devfeed_core.source_enrichment import request_enrichment
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import event, select, update
from sqlalchemy.dialects.postgresql import insert

from tests.infrastructure.test_observability import observed_runtime as observed_runtime
from tests.infrastructure.test_observability import otel_points
from tests.support.user_personalization import user_data as user_data
from tests.users.accounts.test_avatar_uploads import picture
from tests.users.accounts.test_avatar_uploads import uploaded_reader as uploaded_reader

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def enable_cache(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    yield
    cache.close_cache()


def approved_source(database):
    with database.begin() as session:
        source = Source(
            name="Example",
            feed_url="https://example.com/rss",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        return source.id


def test_repeated_get_performs_no_sql_on_hit(client):
    statements = []

    def capture(*args):
        statements.append(args[2])

    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        assert client.get("/v1/feed").headers["x-cache"] == "MISS"
        count = len(statements)
        assert count > 0
        assert client.get("/v1/feed").headers["x-cache"] == "HIT"
        assert len(statements) == count
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)


def test_api_taxonomy_write_invalidates_cached_reads(client, admin_client):
    assert client.get("/v1/tags").json() == []
    assert client.get("/v1/tags").headers["x-cache"] == "HIT"
    assert (
        admin_client.post("/v1/admin/tags", json={"name": "Python", "slug": "python"}).status_code
        == 201
    )
    result = client.get("/v1/tags")
    assert result.headers["x-cache"] == "MISS" and result.json() == []


def test_review_commit_invalidates_but_rollback_and_polling_do_not(database, client):
    identifier = approved_source(database)
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    with database.begin() as session:
        session.get(Source, identifier).last_success_at = utcnow()
    assert client.get(path).headers["x-cache"] == "HIT"
    with database() as session:
        services.review_source(
            session, identifier, SourceDecision(decision="rejected", note="test")
        )
        session.rollback()
    assert client.get(path).headers["x-cache"] == "HIT"
    with database.begin() as session:
        services.review_source(
            session, identifier, SourceDecision(decision="rejected", note="test")
        )
    assert client.get(path).status_code == 404


def test_worker_ingestion_images_and_profiles_invalidate_cached_reads(
    database, client, rss_bytes, monkeypatch, publish_for_read_test
):
    monkeypatch.setattr(get_settings(), "published_article_refresh_enabled", True)
    identifier = approved_source(database)
    assert client.get("/v1/feed").json()["items"] == []
    with database.begin() as session:
        source = session.get(Source, identifier)
        job_id = request_ingestion(session, source).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(str(job_id))
    assert client.get("/v1/feed").json()["items"] == []
    publish_for_read_test()
    feed = client.get("/v1/feed")
    assert feed.headers["x-cache"] == "MISS" and len(feed.json()["items"]) == 2
    with database.begin() as session:
        image_job = session.scalar(select(ArticleImageJob))
        article_id = image_job.article_id
        assert image_job.status == "succeeded"
    path = f"/v1/articles/{article_id}"
    assert client.get(path).json()["image_url"] is None
    assert client.get(path).headers["x-cache"] == "HIT"
    with database.begin() as session:
        article = session.get(Article, article_id)
        request_processing_retry(
            session,
            article_id,
            target="images",
            expected_revision=article.editorial_revision,
            expected_job_id=image_job.id,
            actor={"subject": "cache-test"},
        )
        image_job = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.article_id == article_id, ArticleImageJob.status == "queued"
            )
        )
        image_id = image_job.id
    pending = client.get(path)
    assert pending.status_code == 200 and pending.headers["x-cache"] == "HIT"
    assert pending.json()["image_url"] is None
    monkeypatch.setattr(
        image_tasks,
        "fetch_page",
        lambda url: FetchResult(
            200, b'<meta property="og:image" content="https://example.com/cover.png">', url
        ),
    )
    image_tasks.enrich_image(str(image_id))
    refreshed = client.get(path)
    assert refreshed.status_code == 200
    assert refreshed.headers["x-cache"] == "MISS"
    assert refreshed.json()["image_url"] == "https://example.com/cover.png"
    source_path = f"/v1/sources/{identifier}"
    assert client.get(source_path).json()["logo_url"] is None
    with database.begin() as session:
        profile_job = request_enrichment(session, identifier).id
    monkeypatch.setattr(
        source_tasks,
        "lookup_profile",
        lambda *args: ({"logo_url": "https://example.com/logo.png"}, None),
    )
    source_tasks.enrich_source(str(profile_job))
    assert client.get(source_path).json()["logo_url"] == "https://example.com/logo.png"


def test_redis_lua_fences_old_generations_and_lock_owners():
    store = cache.get_cache()
    owner = store.lookup(str(uuid.uuid4()), "public")
    assert store.publish(owner, b"[]", 10)
    assert store.redis.ttl(owner.key) > 0
    store.invalidate()
    assert not store.publish(owner, b'["old"]', 10)
    # Another loader replacing an expired token must not be unlocked by old work.
    store.redis.set(owner.lock_key, "replacement", ex=10)
    store.release(owner)
    assert store.redis.get(owner.lock_key) == b"replacement"


def test_feed_sequence_retry_after_lost_reply_preserves_original_order_and_expiry(monkeypatch):
    store = cache.get_feed_sequence_cache()
    identity = f"retry:{uuid.uuid4()}"
    key = f"{store.namespace}:feed:{identity}"
    original_eval = store.redis.eval
    attempts = []

    def lose_first_reply(*args):
        result = original_eval(*args)
        attempts.append(True)
        if len(attempts) == 1:
            raise RedisTimeoutError()
        return result

    clock = [100.0]
    monkeypatch.setattr(cache, "time", type("Clock", (), {"monotonic": lambda: clock[0]}))
    monkeypatch.setattr(store.redis, "eval", lose_first_reply)
    with pytest.raises(cache.CacheUnavailable):
        store.write_sequence(identity, [b"first", b"second"], 60)
    assert store.redis.lrange(key, 0, -1) == [b"first", b"second"]
    assert 0 < store.redis.ttl(key) <= 60
    # Prove a retry neither changes existing entries nor resets their retention.
    store.redis.pexpire(key, 3000)
    clock[0] += cache.RETRY_SECONDS + 1
    store.write_sequence(identity, [b"replacement", b"extra", b"third"], 10800)
    assert len(attempts) == 2
    assert store.read_sequence(identity, 0, 0) == [b"first"]
    assert store.read_sequence(identity, 1, 9) == [b"second"]
    assert 0 < store.redis.pttl(key) <= 3000


def test_duplicate_upsert_and_zero_row_update_do_not_invalidate(database, client):
    identifier = approved_source(database)
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    with database.begin() as session:
        result = session.scalar(
            insert(Source)
            .values(name="Duplicate", feed_url="https://example.com/rss", source_type="publisher")
            .on_conflict_do_nothing(index_elements=[Source.feed_url])
            .returning(Source.id)
        )
        assert result is None and DIRTY not in session.info
        result = session.scalar(
            update(Source)
            .where(Source.id == uuid.uuid4())
            .values(name="Unmatched")
            .returning(Source.id)
        )
        assert result is None and DIRTY not in session.info
    assert client.get(path).headers["x-cache"] == "HIT"
    with database.begin() as session:
        result = session.scalar(
            update(Source)
            .where(Source.id == identifier)
            .values(name="Changed")
            .returning(Source.id)
        )
        assert result == identifier and session.info[DIRTY]
    response = client.get(path)
    assert response.headers["x-cache"] == "MISS" and response.json()["name"] == "Changed"


def test_invalidation_metrics_follow_commits_and_affected_rows(database, observed_runtime):
    runtime, _ = observed_runtime
    with database.begin() as session:
        source = Source(name="Example", feed_url="https://example.com/rss", source_type="publisher")
        session.add(source)
        session.add_all([Tag(name="Python", slug="python"), Tag(name="Rust", slug="rust")])
        session.flush()
        identifier = source.id
        assert session.info[REASONS] == {"sources", "tags"}
        assert not otel_points(runtime, "devfeed.cache.invalidations")
    assert sum(point.value for point in otel_points(runtime, "devfeed.cache.invalidations")) == 1
    causes = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert {point.attributes["cache.invalidation_reason"]: point.value for point in causes} == {
        "sources": 1,
        "tags": 1,
    }
    with database() as session:
        session.execute(update(Source).where(Source.id == identifier).values(name="Rolled back"))
        assert session.info[REASONS] == {"sources"}
        session.rollback()
        assert REASONS not in session.info
        session.commit()
    with database.begin() as session:
        session.get(Source, identifier).last_success_at = utcnow()
        session.execute(update(Source).where(Source.id == uuid.uuid4()).values(name="No match"))
        session.execute(
            insert(Source)
            .values(name="Duplicate", feed_url="https://example.com/rss", source_type="publisher")
            .on_conflict_do_nothing(index_elements=[Source.feed_url])
            .returning(Source.id)
        )
        assert REASONS not in session.info
    assert sum(point.value for point in otel_points(runtime, "devfeed.cache.invalidations")) == 1
    with database.begin() as session:
        assert (
            session.scalar(
                update(Source)
                .where(Source.id == identifier)
                .values(name="Changed")
                .returning(Source.id)
            )
            == identifier
        )
        assert session.info[REASONS] == {"sources"}
    assert sum(point.value for point in otel_points(runtime, "devfeed.cache.invalidations")) == 2
    causes = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert {point.attributes["cache.invalidation_reason"]: point.value for point in causes} == {
        "sources": 2,
        "tags": 1,
    }


def test_profile_edit_reports_its_invalidation_cause(user_data, observed_runtime):
    runtime, _ = observed_runtime
    client, *_ = user_data
    assert client.put("/v1/user/settings/profile", json={"bio": "Reader"}).status_code == 200
    points = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert [
        (point.attributes["cache.outcome"], point.value)
        for point in points
        if point.attributes["cache.invalidation_reason"] == "profile"
    ] == [("success", 1)]


def test_avatar_causes_follow_successful_upload_and_removal(
    uploaded_reader, observed_runtime, monkeypatch
):
    from devfeed_user_api.avatar_storage import AvatarStorage

    runtime, _ = observed_runtime
    client, storage, _, identifier = uploaded_reader
    # The avatar fixture reloads settings for its disposable object store.
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    path = "/v1/user/settings/profile/avatar"
    assert (
        client.post(path, files={"file": ("avatar.png", picture(), "image/png")}).status_code == 200
    )
    storage.fail_next = f"{AvatarStorage(identifier).prefix}/128.webp"
    assert (
        client.post(path, files={"file": ("avatar.png", picture(), "image/png")}).status_code == 503
    )
    assert client.delete(path).status_code == 200
    points = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert [
        (point.attributes["cache.outcome"], point.value)
        for point in points
        if point.attributes["cache.invalidation_reason"] == "avatar"
    ] == [("success", 2)]


def test_search_index_cause_requires_a_completed_nonempty_batch(database, observed_runtime):
    from unittest.mock import Mock

    runtime, _ = observed_runtime
    with database.begin() as session:
        session.add(Tag(name="Python", slug="python"))
    engine = Mock()
    engine.sync.side_effect = SearchUnavailable("offline")
    with pytest.raises(SearchUnavailable):
        sync_batch(database, engine)
    engine.sync.side_effect = None
    assert sync_batch(database, engine) > 0
    assert sync_batch(database, engine) == 0
    points = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert [
        (point.attributes["cache.outcome"], point.value)
        for point in points
        if point.attributes["cache.invalidation_reason"] == "search_index"
        and point.attributes["cache.domain"] == "public"
    ] == [("success", 1)]


def test_projection_only_indexing_keeps_content_cache_but_fences_search(
    database, client, monkeypatch
):
    from unittest.mock import Mock

    from devfeed_core.models import SearchEvent

    monkeypatch.setattr(get_settings(), "search_cache_isolation_enabled", True)
    identifier = approved_source(database)
    engine = Mock()
    assert sync_batch(database, engine) > 0
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    assert client.get(path).headers["x-cache"] == "HIT"
    store = cache.get_cache()
    search = store.lookup("search-result", "public", dependencies=("search-index",))
    content = store.lookup("unrelated-publisher", "public")
    with database.begin() as session:
        session.add(SearchEvent(kind="sources", entity_id=identifier, public_change=False))
    assert sync_batch(database, engine) == 1
    assert client.get(path).headers["x-cache"] == "HIT"
    assert not store.publish(search, b'{"items":[]}', 30)
    assert store.publish(content, b'{"title":"Approved"}', 30)


@pytest.mark.parametrize("reject", [False, True])
def test_raw_sql_changes_keep_the_conservative_indexer_invalidation(
    database, client, monkeypatch, reject
):
    from unittest.mock import Mock

    from devfeed_core.models import SearchEvent
    from sqlalchemy import text

    monkeypatch.setattr(get_settings(), "search_cache_isolation_enabled", True)
    identifier = approved_source(database)
    engine = Mock()
    assert sync_batch(database, engine) > 0
    path = f"/v1/sources/{identifier}"
    assert client.get(path).json()["name"] == "Example"
    assert client.get(path).headers["x-cache"] == "HIT"
    old = cache.get_cache().lookup("late-source-reader", "public")
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE sources SET approval_status='rejected' WHERE id=:id")
            if reject
            else text("UPDATE sources SET name='Raw SQL' WHERE id=:id"),
            {"id": identifier},
        )
    with database() as session:
        assert session.scalar(select(SearchEvent.public_change)) is True
    assert client.get(path).headers["x-cache"] == "HIT"
    assert sync_batch(database, engine) == 1
    result = client.get(path)
    if reject:
        assert result.status_code == 404
    else:
        assert result.headers["x-cache"] == "MISS" and result.json()["name"] == "Raw SQL"
    assert not cache.get_cache().publish(old, b'{"name":"Old"}', 30)


def test_backfill_fences_unknown_data_once_and_marks_projection_work(database, client, monkeypatch):
    from unittest.mock import Mock

    from devfeed_core.models import SearchEvent
    from devfeed_core.search_index import backfill
    from sqlalchemy import text

    monkeypatch.setattr(get_settings(), "search_cache_isolation_enabled", True)
    identifier = approved_source(database)
    assert sync_batch(database, Mock()) > 0
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    with get_engine().begin() as connection:
        # Represent an explicit repair after direct maintenance; the backfill
        # itself must provide the conservative public fence before page drains.
        connection.execute(
            text("UPDATE sources SET description='Repair' WHERE id=:id"), {"id": identifier}
        )
        connection.execute(text("DELETE FROM search_events"))
    assert client.get(path).headers["x-cache"] == "HIT"
    assert backfill(database) > 0
    assert client.get(path).headers["x-cache"] == "MISS"
    with database() as session:
        assert all(value is False for value in session.scalars(select(SearchEvent.public_change)))
    assert sync_batch(database, Mock()) > 0
    assert client.get(path).headers["x-cache"] == "HIT"


@pytest.mark.parametrize("isolation", [False, True])
def test_projection_pages_do_not_repeat_unrelated_api_queries(
    database, client, monkeypatch, isolation
):
    from unittest.mock import Mock

    from devfeed_core.models import SearchEvent

    monkeypatch.setattr(get_settings(), "search_cache_isolation_enabled", isolation)
    identifier = approved_source(database)
    engine = Mock()
    assert sync_batch(database, engine) > 0
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    statuses, queries = [], []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    for _ in range(20):
        with database.begin() as session:
            session.add(SearchEvent(kind="sources", entity_id=identifier, public_change=False))
        assert sync_batch(database, engine) == 1
        event.listen(get_engine(), "before_cursor_execute", observed)
        try:
            statuses.append(client.get(path).headers["x-cache"])
        finally:
            event.remove(get_engine(), "before_cursor_execute", observed)
    assert statuses == ["HIT" if isolation else "MISS"] * 20
    assert len(queries) == (0 if isolation else 20)
