"""Untrusted queries stay text, and analytics require a bounded search receipt."""

import json
import uuid
from types import SimpleNamespace

import pytest
from devfeed_core.search_clicks import CLICK_TOKEN_TTL, SearchClickTokens
from devfeed_core.search_suggestions import normalize_query
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import SQLAlchemyError

RESULT_ID = uuid.UUID("8e65d3cd-ef29-455a-88c6-990b96da9479")
PROBE = '"><asdf alt="">-f3f3'


@pytest.mark.parametrize("value", ["python\0", "ﬃ" * 200, "word " * 21, "x" * 201])
def test_search_and_suggestions_reject_invalid_input_before_dependencies_run(value, monkeypatch):
    from devfeed_api import search
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    app = create_app()
    calls = []
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        execute=lambda *args: calls.append(args)
    )
    monkeypatch.setattr(search, "Typesense", lambda: pytest.fail("invalid query reached index"))
    with TestClient(app) as client:
        for path in ["/v1/search", "/v1/search/suggestions"]:
            assert client.get(path, params={"q": value}).status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    "value", [PROBE, "SELECT * FROM users;", "<script>alert(1)</script>", "日本語", "C++"]
)
def test_search_text_is_not_a_sql_or_html_blacklist(value, monkeypatch):
    from devfeed_api import search
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    app = create_app()
    calls = []
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(execute=lambda *args: None)

    def query(q, kinds, page, **kwargs):
        calls.append(q)
        return {kind: {"hits": [], "found": 0} for kind in kinds}

    monkeypatch.setattr(search, "Typesense", lambda: SimpleNamespace(search=query))
    with TestClient(app) as client:
        response = client.get("/v1/search", params={"q": value})
    assert response.status_code == 200
    assert response.json()["query"] == value
    assert calls == [value]


def test_canonicalization_validates_after_nfkc_and_casefold():
    assert normalize_query("  Ｐｙｔｈｏｎ\tSQL ") == "python sql"
    assert normalize_query("  Ｐｙｔｈｏｎ\tSQL ", casefold=False) == "Python SQL"
    assert normalize_query("?!", allow_empty=True) == "?!"
    assert normalize_query("", allow_empty=True) == ""
    assert normalize_query("ﬃ" * 66) == "ffi" * 66
    for value in ["ﬃ" * 67, "ß" * 101, "\0", "!!!", ""]:
        with pytest.raises(ValueError):
            normalize_query(value)


@pytest.mark.parametrize("value", ["\ud800", "before\udfffafter"])
def test_query_normalization_rejects_unpaired_surrogates(value):
    with pytest.raises(ValueError, match="Text must be valid Unicode"):
        normalize_query(value)
    assert normalize_query("Unicode 東京 🙂 𐐀", casefold=False) == "Unicode 東京 🙂 𐐀"


def test_receipts_are_bound_to_canonical_query_kind_result_key_and_expiry():
    clock = [1700000000.0]
    signer = SearchClickTokens("server-only-test-key", clock=lambda: clock[0])
    token = signer.issue("  Ｐｙｔｈｏｎ\tSQL ", "articles", RESULT_ID)
    assert "Python" not in token and "server-only" not in token
    assert signer.valid(token, "Python SQL", "articles", RESULT_ID)
    assert not signer.valid(token, "python SQL", "articles", RESULT_ID)
    assert not signer.valid(token, "unmatched query", "articles", RESULT_ID)
    assert not signer.valid(token, "Python SQL", "topics", RESULT_ID)
    assert not signer.valid(token, "Python SQL", "articles", uuid.uuid4())
    assert not SearchClickTokens("rotated-key", clock=lambda: clock[0]).valid(
        token, "Python SQL", "articles", RESULT_ID
    )
    # Expiry cannot be edited independently of the signature.
    expires, signature = token.split(".")
    assert not signer.valid(f"{int(expires) + 1}.{signature}", "Python SQL", "articles", RESULT_ID)
    assert not signer.valid(
        f"{int(expires) + CLICK_TOKEN_TTL}.{signature}", "Python SQL", "articles", RESULT_ID
    )
    for malformed in ["", "1.x", "not-a-token", token + "\n", "１." + signature]:
        assert not signer.valid(malformed, "Python SQL", "articles", RESULT_ID)
    assert not signer.valid(token, "python\0", "articles", RESULT_ID)
    clock[0] += CLICK_TOKEN_TTL
    assert not signer.valid(token, "Python SQL", "articles", RESULT_ID)


@pytest.fixture
def click_api(monkeypatch):
    from devfeed_api import search
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app
    from devfeed_core.config import get_settings

    get_settings().search_query_key = SecretStr("query-only-test-key")
    calls = {"sql": [], "records": [], "aggregates": [], "commits": [], "budgets": []}
    state = {"retry": 0, "visible": True, "redis_error": False, "sql_error": False}

    def execute(*args):
        if state["sql_error"]:
            raise SQLAlchemyError("private SQL detail")
        calls["sql"].append(args)

    session = SimpleNamespace(execute=execute, commit=lambda: calls["commits"].append(True))
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    def records(session, kind, ids):
        calls["records"].append((kind, ids))
        return {RESULT_ID: object()} if state["visible"] else {}

    def eval_limit(*args):
        calls["budgets"].append(args)
        if state["redis_error"]:
            raise RedisConnectionError("private Redis host")
        return state["retry"]

    monkeypatch.setattr(search, "public_records", records)
    monkeypatch.setattr(
        search, "record_successful_query", lambda s, q: calls["aggregates"].append(q)
    )
    monkeypatch.setattr(search, "get_rate_limit_redis", lambda: SimpleNamespace(eval=eval_limit))
    body = {
        "query": "Python SQL",
        "result_kind": "articles",
        "result_id": str(RESULT_ID),
        "click_token": search.click_tokens().issue("Python SQL", "articles", RESULT_ID),
    }
    with TestClient(app) as client:
        yield client, body, calls, state


def test_click_with_receipt_rechecks_visibility_and_records_an_identity_free_aggregate(click_api):
    client, body, calls, state = click_api
    response = client.post("/v1/search/analytics/click", json=body)
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert calls["records"] == [("articles", [RESULT_ID])]
    assert calls["aggregates"] == ["python sql"]
    assert calls["commits"] == [True]
    assert len(calls["budgets"]) == 1
    _, key_count, *args = calls["budgets"][0]
    assert key_count == 4
    assert args[-8:] == [120, 60, 1000, 3600, 20, 60, 100, 3600]
    assert all("Python" not in key and "SQL" not in key for key in args[:4])
    assert calls["sql"][0][1] == {"timeout": "500"}


@pytest.mark.parametrize(
    "change",
    [
        {"query": "zero-hit forged text"},
        {"result_kind": "topics"},
        {"result_id": str(uuid.uuid4())},
        {"click_token": "forged"},
    ],
)
def test_forged_clicks_do_not_use_redis_or_database(click_api, change):
    client, body, calls, state = click_api
    response = client.post("/v1/search/analytics/click", json={**body, **change})
    assert response.status_code == 403
    assert all(not value for value in calls.values())


def test_legacy_click_is_compatible_but_cannot_write_arbitrary_queries(click_api):
    client, body, calls, state = click_api
    del body["click_token"]
    response = client.post("/v1/search/analytics/click", json=body)
    assert response.status_code == 204
    assert all(not value for value in calls.values())


@pytest.mark.parametrize("value", ["bad\0input", "ﬃ" * 200, "word " * 21])
def test_invalid_click_query_never_consumes_a_budget(click_api, value):
    client, body, calls, state = click_api
    response = client.post("/v1/search/analytics/click", json={**body, "query": value})
    assert response.status_code == 422
    assert all(not value for value in calls.values())


@pytest.mark.parametrize("value", ["\ud800", "before\udfffafter"])
def test_escaped_invalid_unicode_click_never_consumes_a_budget(click_api, value):
    client, body, calls, state = click_api
    response = client.post(
        "/v1/search/analytics/click",
        content=json.dumps({**body, "query": value}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] in {"value_error", "string_unicode"}
    assert all(not value for value in calls.values())


def test_unpublished_result_cannot_increment_statistics(click_api):
    client, body, calls, state = click_api
    state["visible"] = False
    assert client.post("/v1/search/analytics/click", json=body).status_code == 403
    assert not calls["aggregates"] and not calls["commits"]


def test_rate_limit_is_shared_and_does_not_open_a_transaction(click_api):
    client, body, calls, state = click_api
    state["retry"] = 42
    response = client.post(
        "/v1/search/analytics/click", json=body, headers={"X-Forwarded-For": "attacker-chosen"}
    )
    assert response.status_code == 429 and response.headers["retry-after"] == "42"
    assert response.headers["cache-control"] == "no-store"
    assert not calls["sql"] and not calls["records"] and not calls["aggregates"]


@pytest.mark.parametrize("failure", ["redis_error", "sql_error"])
def test_unavailable_dependencies_fail_closed_without_leaking_details(click_api, failure):
    client, body, calls, state = click_api
    state[failure] = True
    response = client.post("/v1/search/analytics/click", json=body)
    assert response.status_code == 503 and response.headers["retry-after"] == "1"
    assert "private" not in response.text
    assert not calls["aggregates"] and not calls["commits"]


def test_search_issues_receipts_only_for_rehydrated_public_hits(click_api, monkeypatch):
    from devfeed_api import search

    client, body, calls, state = click_api
    private_id = uuid.uuid4()
    monkeypatch.setattr(
        search,
        "Typesense",
        lambda: SimpleNamespace(
            search=lambda *a, **kw: {
                "articles": {
                    "hits": [
                        {"document": {"id": str(RESULT_ID)}},
                        {"document": {"id": str(private_id)}},
                    ],
                    "found": 2,
                }
            }
        ),
    )
    monkeypatch.setattr(
        search,
        "hit",
        lambda kind, record: {
            "id": RESULT_ID,
            "title": "Python SQL",
            "description": "Query tips",
            "href": "/articles/test",
            "image_url": None,
            "label": "Article",
            "published_at": None,
        },
    )
    response = client.get("/v1/search", params={"q": "  Python\tSQL ", "section": "articles"})
    assert response.status_code == 200
    [item] = response.json()["sections"]["articles"]["items"]
    assert search.click_tokens().valid(item["click_token"], "Python SQL", "articles", RESULT_ID)
    assert (
        client.post(
            "/v1/search/analytics/click", json={**body, "click_token": item["click_token"]}
        ).status_code
        == 204
    )


def test_click_body_limit_rejects_direct_api_requests_before_db_dependency(monkeypatch):
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail(
        "oversized body reached dependencies"
    )
    with TestClient(app) as client:
        for content in [b"x" * 4097, "é" * 2049]:
            response = client.post("/v1/search/analytics/click", content=content)
            assert response.status_code == 413 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("sentinel", [False, True])
def test_rate_client_uses_short_timeouts_without_retry_backoff(sentinel):
    from devfeed_api.dependencies import get_rate_limit_redis
    from devfeed_core.config import get_settings

    config = get_settings()
    if sentinel:
        config.redis_sentinel_nodes = [("sentinel.invalid", 26379)]
        config.redis_sentinel_master = "test"
    get_rate_limit_redis.cache_clear()
    try:
        client = get_rate_limit_redis()
        assert client is get_rate_limit_redis()
        pools = [client.connection_pool]
        if sentinel:
            pools.extend(
                node.connection_pool for node in client.connection_pool.sentinel_manager.sentinels
            )
        for pool in pools:
            options = pool.connection_kwargs
            assert options["socket_connect_timeout"] == 0.2
            assert options["socket_timeout"] == 0.2
            assert options["retry"]._retries == 0
    finally:
        client.close()
        get_rate_limit_redis.cache_clear()


def test_click_receipts_fail_closed_without_search_key(click_api):
    from devfeed_core.config import get_settings

    client, body, calls, state = click_api
    get_settings().search_query_key = None
    assert client.post("/v1/search/analytics/click", json=body).status_code == 403
    assert all(not value for value in calls.values())


def test_escaped_json_for_a_maximum_unicode_query_fits_click_byte_limit(click_api):
    import json

    client, body, calls, state = click_api
    value = "𐐀" * 200
    event = {**body, "query": value, "click_token": None}
    encoded = json.dumps(event, ensure_ascii=True)
    assert 2048 < len(encoded.encode()) < 4096
    assert (
        client.post(
            "/v1/search/analytics/click",
            content=encoded,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 204
    )
    assert all(not value for value in calls.values())
