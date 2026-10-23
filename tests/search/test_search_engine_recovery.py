"""Search adapter contracts for incomplete upstream responses and write recovery."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core import search_engine
from devfeed_core.config import get_settings
from devfeed_core.search_engine import SearchRequestError, SearchUnavailable, Typesense
from pydantic import SecretStr


@pytest.fixture
def engine(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "search_enabled", True)
    monkeypatch.setattr(settings, "search_url", "http://search:8108")
    monkeypatch.setattr(settings, "search_admin_key", SecretStr("private-index-key"))
    monkeypatch.setattr(settings, "search_index_retry_attempts", 1)
    monkeypatch.setattr(search_engine.time, "sleep", lambda _: None)
    return Typesense(admin=True)


@pytest.fixture
def upstream(monkeypatch):
    """Exercise real HTTP parsing and pooling with an in-memory network backend."""
    pools = []

    def respond(*responses):
        packets = []
        for status, body in responses:
            packets.append(
                f"HTTP/1.1 {status} Response\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
            )
        pool = httpcore.ConnectionPool(network_backend=httpcore.MockBackend(packets))
        pools.append(pool)
        monkeypatch.setattr(search_engine, "_get_connection_pool", lambda: pool)

    yield respond
    for pool in pools:
        pool.close()


@pytest.mark.parametrize(
    "result",
    [
        {"results": []},
        {"results": [{"error": "private-index-key"}]},
        {"results": [{"hits": [], "search_cutoff": True}]},
        {"results": [{}]},
        {"results": [{"hits": {}}]},
        {"results": [{"hits": [{"document": {"id": "article"}}] * 13}]},
        {"results": None},
    ],
    ids=[
        "missing-section",
        "section-error",
        "cutoff",
        "missing-hits",
        "invalid-hits",
        "too-many-hits",
        "null-results",
    ],
)
def test_search_rejects_incomplete_results_without_exposing_upstream_details(
    engine, upstream, result
):
    upstream((200, json.dumps(result).encode()))

    with pytest.raises(SearchUnavailable) as raised:
        engine.search("python", ("articles",))

    assert str(raised.value) in {"Search is unavailable", "Search is temporarily unavailable"}
    assert "private-index-key" not in str(raised.value)


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpcore.ReadTimeout, "timeout"),
        (httpcore.ConnectError, "network_error"),
        (httpcore.RemoteProtocolError, "network_error"),
        (ValueError, "response_error"),
    ],
)
def test_transport_failures_keep_retry_classification_and_hide_credentials(
    engine, monkeypatch, failure, reason
):
    pool = Mock()
    pool.stream.side_effect = failure("http://search:8108 private-index-key")
    monkeypatch.setattr(search_engine, "_get_connection_pool", lambda: pool)

    with pytest.raises(SearchRequestError, match="^Search is unavailable$") as raised:
        engine.request("GET", "/health")

    assert raised.value.reason == reason
    assert raised.value.status is None


def test_oversized_http_response_is_rejected(engine, upstream):
    upstream((200, b"x" * 4_000_001))

    with pytest.raises(SearchUnavailable, match="Search response exceeded its limit"):
        engine.request("GET", "/collections/devfeed_articles_v1")


def test_invalid_http_response_is_sanitized(engine, monkeypatch):
    with httpcore.ConnectionPool(
        network_backend=httpcore.MockBackend([b"private-index-key\r\n"])
    ) as pool:
        monkeypatch.setattr(search_engine, "_get_connection_pool", lambda: pool)
        with pytest.raises(SearchRequestError, match="^Search is unavailable$") as raised:
            engine.request("GET", "/health")

    assert raised.value.reason == "network_error"


@pytest.mark.parametrize("operation", ["count", "sync"])
def test_missing_alias_prevents_collection_access_or_writes(
    engine, upstream, monkeypatch, operation
):
    upstream((404, b'{"message":"Not Found"}'))
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)

    with pytest.raises(SearchUnavailable, match="Search alias is unavailable"):
        if operation == "count":
            engine.count("articles")
        else:
            engine.sync("articles", [{"id": "article"}], {"removed"})

    assert request.call_count == 1
    assert request.call_args.args == ("GET", "/aliases/devfeed_articles")


@pytest.mark.parametrize(
    "payload", [b"not-json", b"{}", b'{"collection_name":""}', b'{"collection_name":3}']
)
def test_malformed_alias_response_is_unavailable(engine, upstream, payload):
    upstream((200, payload))

    with pytest.raises(SearchUnavailable, match="Search alias is unavailable"):
        engine.alias_target("articles")


@pytest.mark.parametrize(
    "payload", [b"not-json", b"{}", b'{"num_documents":-1}', b'{"num_documents":"2"}']
)
def test_count_recovers_from_an_untrustworthy_collection_count(engine, upstream, payload):
    upstream(
        (200, b'{"collection_name":"devfeed_articles_v2"}'),
        (200, payload),
        (200, b'{"num_documents":2}'),
    )

    assert engine.count("articles") == 2


@pytest.mark.parametrize("payload", [b"not-json", b"null", b'{"fields":null}'])
def test_unreadable_schema_does_not_attempt_a_schema_update(engine, upstream, monkeypatch, payload):
    upstream((200, payload))
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)

    with pytest.raises(SearchUnavailable, match="Search schema is unavailable"):
        engine.ensure_filter_fields("articles")

    assert request.call_count == 1
    assert request.call_args.args == ("GET", "/collections/devfeed_articles_v1")


def test_existing_schema_gets_only_missing_filter_fields(engine, upstream, monkeypatch):
    upstream(
        (200, b'{"fields":[{"name":"topics"},{"name":"sources"},{"name":"tags"}]}'), (200, b"{}")
    )
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)

    engine.ensure_filter_fields("articles")

    assert request.call_args.args == ("PATCH", "/collections/devfeed_articles_v1")
    assert request.call_args.kwargs["data"] == {
        "fields": [{"name": "content_type", "type": "string", "facet": True, "optional": True}]
    }


@pytest.mark.parametrize(
    ("payload", "outcome"),
    [(b"", "malformed"), (b"null", "malformed"), (b'{"success":false}', "rejected")],
)
def test_import_recovery_acknowledges_documents_before_deleting_and_records_outcomes(
    engine, upstream, monkeypatch, payload, outcome
):
    upstream(
        (200, b'{"collection_name":"devfeed_articles_v2"}'),
        (200, payload),
        (200, b'{"success":true}'),
        (200, b'{"num_deleted":1}'),
    )
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)
    metrics = []
    monkeypatch.setattr(
        search_engine,
        "_metric_instruments",
        lambda: {
            "search_documents": SimpleNamespace(
                add=lambda count, labels: metrics.append((count, labels))
            )
        },
    )

    engine.sync("articles", [{"id": "article"}], {"removed"})

    assert [call.args[0] for call in request.call_args_list] == ["GET", "POST", "POST", "DELETE"]
    assert all("devfeed_articles_v2" in call.args[1] for call in request.call_args_list[1:])
    assert metrics == [
        (1, {"outcome": outcome}),
        (1, {"outcome": "retried"}),
        (1, {"outcome": "imported"}),
        (1, {"outcome": "deleted"}),
    ]


def test_permanent_import_http_error_does_not_retry_or_delete(engine, upstream, monkeypatch):
    upstream(
        (200, b'{"collection_name":"devfeed_articles_v2"}'),
        (400, b'{"message":"private-index-key"}'),
    )
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)

    with pytest.raises(SearchRequestError, match="^Search is unavailable$") as raised:
        engine.sync("articles", [{"id": "article"}], {"removed"})

    assert raised.value.status == 400
    assert raised.value.reason == "http_error"
    assert [call.args[0] for call in request.call_args_list] == ["GET", "POST"]


@pytest.mark.parametrize("payload", [b'[{"name":"popular"}]', b'{"rules":[{"name":"popular"}]}'])
def test_analytics_rules_accept_both_supported_response_shapes(engine, upstream, payload):
    upstream((200, payload))

    assert engine.analytics_rules() == [{"name": "popular"}]


@pytest.mark.parametrize("payload", [b"not-json", b"null", b'{"rules":{}}'])
def test_invalid_analytics_rules_fail_with_a_safe_error(engine, upstream, payload):
    upstream((200, payload))

    with pytest.raises(SearchUnavailable, match="Search analytics is unavailable"):
        engine.analytics_rules()


@pytest.mark.parametrize("payload", [b"not-json", b"null", b'{"hits":{}}', b'{"hits":[null]}'])
def test_malformed_analytics_documents_fail_with_a_safe_error(engine, upstream, payload):
    upstream((200, payload))

    with pytest.raises(SearchUnavailable, match="Search analytics is unavailable"):
        engine.analytics_queries("devfeed_search_queries")
