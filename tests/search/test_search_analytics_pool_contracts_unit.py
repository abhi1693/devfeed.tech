"""Search bootstrap and concurrent transport ownership preserve live index contracts."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core import search_engine as search
from devfeed_core.search_engine import SearchImportError, SearchRequestError, Typesense
from pydantic import SecretStr

from tests.search.test_search_setup_unit import upstream as upstream
from tests.support.source_readiness import configuration


@pytest.fixture
def engine(monkeypatch):
    settings = configuration(
        search_enabled=True,
        search_url="http://search:8108",
        search_admin_key=SecretStr("private-index-key"),
        search_analytics_enabled=True,
        search_index_retry_attempts=0,
    )
    monkeypatch.setattr(search, "get_settings", lambda: settings)
    return Typesense(admin=True)


@pytest.mark.parametrize("collection_status", [201, 409])
def test_analytics_bootstrap_accepts_new_or_existing_destinations_before_installing_rules(
    monkeypatch, engine, upstream, collection_status
):
    upstream((collection_status, b"{}"), (collection_status, b"{}"), (200, b"{}"), (200, b"{}"))
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)

    engine.setup_analytics()

    calls = request.call_args_list
    assert [call.args for call in calls] == [
        ("POST", "/collections"),
        ("POST", "/collections"),
        ("PUT", "/analytics/rules/devfeed-popular-queries"),
        ("PUT", "/analytics/rules/devfeed-nohits-queries"),
    ]
    destinations = [f"{engine.prefix}_search_queries", f"{engine.prefix}_search_nohits"]
    for call, destination in zip(calls[:2], destinations, strict=True):
        assert call.kwargs["data"] == {
            "name": destination,
            "fields": [{"name": "q", "type": "string"}, {"name": "count", "type": "int32"}],
            "default_sorting_field": "count",
        }
    for call, destination in zip(calls[2:], destinations, strict=True):
        assert call.kwargs["data"]["collection"] == engine.alias("articles")
        assert call.kwargs["data"]["params"]["destination_collection"] == destination


def test_failed_analytics_destination_does_not_install_rules_or_reveal_upstream_credentials(
    monkeypatch, engine, upstream
):
    upstream((201, b"{}"), (503, b'{"message":"private-index-key"}'))
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)

    with pytest.raises(SearchRequestError) as failure:
        engine.setup_analytics()

    assert failure.value.status == 503 and str(failure.value) == "Search is unavailable"
    assert [call.args for call in request.call_args_list] == [("POST", "/collections")] * 2


def test_concurrent_first_requests_share_one_process_pool(monkeypatch):
    """Both callers reach the real lock before either can initialize the pool."""
    rendezvous = threading.Barrier(2)
    lock = threading.Lock()
    created = []

    class InitialRace:
        def __enter__(self):
            rendezvous.wait(timeout=5)
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    def construct(**options):
        pool = httpcore.ConnectionPool(**options)
        created.append(pool)
        return pool

    monkeypatch.setattr(search, "_pool", None)
    monkeypatch.setattr(search, "_pool_pid", None)
    monkeypatch.setattr(search, "_pool_lock", InitialRace())
    monkeypatch.setattr(search, "httpcore", SimpleNamespace(ConnectionPool=construct))
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(search._get_connection_pool) for _ in range(2)]
            first, second = [future.result(timeout=6) for future in futures]
        assert first is second and created == [first]
        assert search._get_connection_pool() is first
    finally:
        monkeypatch.setattr(search, "_pool_lock", lock)
        search._close_connection_pool()
        for pool in created:
            pool.close()


def test_deletion_only_batch_uses_live_alias_without_requiring_import_or_metrics(
    monkeypatch, engine, upstream
):
    removed = uuid.uuid4()
    upstream((200, b'{"collection_name":"devfeed_articles_v9"}'), (200, b'{"num_deleted":1}'))
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)
    monkeypatch.setattr(search, "_metric_instruments", lambda: None)

    engine.sync("articles", [], {removed})

    calls = request.call_args_list
    assert [call.args[0] for call in calls] == ["GET", "DELETE"]
    assert calls[1].args[1].startswith("/collections/devfeed_articles_v9/documents?")
    assert str(removed) in calls[1].args[1]


def test_malformed_import_blocks_deletion_even_when_metrics_are_unavailable(
    monkeypatch, engine, upstream
):
    upstream((200, b'{"collection_name":"devfeed_articles_v9"}'), (200, json.dumps(None).encode()))
    request = Mock(wraps=engine.request)
    monkeypatch.setattr(engine, "request", request)
    monkeypatch.setattr(search, "_metric_instruments", lambda: None)

    with pytest.raises(SearchImportError) as failure:
        engine.sync("articles", [{"id": str(uuid.uuid4())}], {uuid.uuid4()})

    assert failure.value.reason == "malformed"
    assert [call.args[0] for call in request.call_args_list] == ["GET", "POST"]
