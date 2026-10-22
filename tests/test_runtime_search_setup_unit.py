"""Search bootstrap preserves live aliases and validates private transport boundaries."""

import json
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core import search_engine as search
from devfeed_core.config import get_settings
from test_search_engine_recovery import engine as engine


@pytest.fixture
def upstream(monkeypatch):
    pools = []

    def respond(*responses):
        packets = [
            (
                f"HTTP/1.1 {status} Response\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + body
            for status, body in responses
        ]

        class Backend(httpcore.MockBackend):
            def connect_tcp(self, *args, **kwargs):
                return httpcore.MockStream([packets.pop(0)])

        pool = httpcore.ConnectionPool(network_backend=Backend([]))
        pools.append(pool)
        monkeypatch.setattr(search, "_get_connection_pool", lambda: pool)

    yield respond
    for pool in pools:
        pool.close()


@pytest.mark.parametrize("existing", [False, True])
def test_setup_extends_the_schema_without_repointing_an_existing_live_generation(
    engine, upstream, monkeypatch, existing
):
    settings = get_settings()
    monkeypatch.setattr(settings, "search_analytics_enabled", False)
    responses = []
    for kind in search.KINDS:
        responses.extend(
            [
                (409, b'{"message":"Already exists"}'),
                (200, json.dumps({"fields": list(search.FILTER_FIELDS)}).encode()),
                (200, json.dumps({"collection_name": f"devfeed_{kind}_previous"}).encode())
                if existing
                else (404, b'{"message":"Not found"}'),
            ]
        )
        if not existing:
            responses.append((200, b"{}"))
    upstream(*responses)
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)
    engine.setup()
    calls = request.call_args_list
    created = [call.kwargs["data"] for call in calls if call.args == ("POST", "/collections")]
    assert {value["name"] for value in created} == {f"devfeed_{kind}_v1" for kind in search.KINDS}
    for value in created:
        assert value["symbols_to_index"] == ["+", "#", "."]
        assert value["token_separators"] == ["-", "_", "/"]
        assert value["default_sorting_field"] == "published_at"
        fields = {field["name"]: field for field in value["fields"]}
        assert fields["published_at"]["type"] == "int64"
        assert fields["topics"]["type"] == "string[]" and fields["topics"]["facet"] is True
    assert not any(call.args[0] == "PATCH" for call in calls)
    aliases = [call for call in calls if call.args[0] == "PUT"]
    if existing:
        assert aliases == []
    else:
        assert [(call.args[1], call.kwargs["data"]) for call in aliases] == [
            (f"/aliases/devfeed_{kind}", {"collection_name": f"devfeed_{kind}_v1"})
            for kind in search.KINDS
        ]


def test_private_analytics_projection_discards_invalid_rows_and_sorts_ties_deterministically(
    engine, upstream
):
    upstream(
        (
            200,
            json.dumps(
                {
                    "hits": [
                        {"document": {"q": "Rust", "count": 4}},
                        {"document": {"q": "Python", "count": 4}},
                        {"document": {"q": "Zig", "count": 0}},
                        {"document": {"q": "private invalid row", "count": -1}},
                        {"document": {"q": "unreported", "count": "3"}},
                        {"document": {"q": 123, "count": 10}},
                    ]
                }
            ).encode(),
        )
    )
    assert engine.analytics_queries("devfeed_search_queries") == [
        {"query": "Python", "count": 4},
        {"query": "Rust", "count": 4},
        {"query": "Zig", "count": 0},
    ]


@pytest.mark.parametrize("method", ["alias", "physical_collection"])
def test_unknown_search_sections_cannot_be_used_as_collection_paths(engine, method):
    with pytest.raises(ValueError, match="Unknown search section"):
        getattr(engine, method)("articles/../../analytics")


@pytest.mark.parametrize("version", ["v1/../../aliases", {"version": "v1"}])
def test_explicit_collection_generation_cannot_inject_a_path_or_an_untyped_version(engine, version):
    with pytest.raises(ValueError, match="Invalid search collection version"):
        engine.physical_collection("articles", version)


def test_connection_pool_is_reused_within_one_process_and_replaced_after_a_fork(monkeypatch):
    monkeypatch.setattr(search, "_pool", None)
    monkeypatch.setattr(search, "_pool_pid", None)
    process_id = [101]
    monkeypatch.setattr(search.os, "getpid", lambda: process_id[0])
    first = search._get_connection_pool()
    assert isinstance(first, httpcore.ConnectionPool)
    try:
        assert search._get_connection_pool() is first
        process_id[0] = 202
        second = search._get_connection_pool()
        assert second is not first and search._get_connection_pool() is second
        search._close_connection_pool()
        assert search._pool is None and search._pool_pid is None
        search._close_connection_pool()
    finally:
        first.close()
        search._close_connection_pool()
