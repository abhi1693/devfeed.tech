"""Typesense analytics projection tests."""

import json

import pytest
from devfeed_core.search_engine import (
    ANALYTICS_NOHITS_COLLECTION,
    ANALYTICS_QUERY_COLLECTION,
    Typesense,
)


def configure_search(monkeypatch):
    from devfeed_core.config import get_settings
    from pydantic import SecretStr

    settings = get_settings()
    monkeypatch.setattr(settings, "search_enabled", True)
    monkeypatch.setattr(settings, "search_url", "http://search:8108")
    monkeypatch.setattr(settings, "search_admin_key", SecretStr("index-only"))
    monkeypatch.setattr(settings, "search_analytics_enabled", True)
    return settings


def test_analytics_queries_returns_sorted_projection_fields(monkeypatch):
    configure_search(monkeypatch)
    engine = Typesense(admin=True)

    def request(method, path, **kwargs):
        assert method == "GET"
        assert path.startswith("/collections/queries/documents/search?")
        assert "sort_by=count%3Adesc" in path
        assert "q%3Aasc" not in path
        return json.dumps(
            {
                "hits": [
                    {"document": {"q": "kubernetes", "count": 9, "ignored": "secret"}},
                    {"document": {"q": "python", "count": 4}},
                    {"document": {"q": "asyncio", "count": 4}},
                    {"document": {"q": "invalid", "count": "4"}},
                ]
            }
        ).encode()

    monkeypatch.setattr(engine, "request", request)

    assert engine.analytics_queries("queries", limit=10) == [
        {"query": "kubernetes", "count": 9},
        {"query": "asyncio", "count": 4},
        {"query": "python", "count": 4},
    ]


def test_setup_analytics_creates_destinations_and_rules(monkeypatch):
    configure_search(monkeypatch)
    engine = Typesense(admin=True)
    collections = []
    requests = []

    def create_collection(collection):
        collections.append(collection)

    def request(method, path, **kwargs):
        requests.append((method, path, kwargs.get("data")))
        return b"{}"

    monkeypatch.setattr(engine, "_create_analytics_collection", create_collection)
    monkeypatch.setattr(engine, "request", request)

    engine.setup_analytics()

    assert collections == [
        f"{engine.prefix}_{ANALYTICS_QUERY_COLLECTION}",
        f"{engine.prefix}_{ANALYTICS_NOHITS_COLLECTION}",
    ]
    assert [path for _, path, _ in requests] == [
        "/analytics/rules/devfeed-popular-queries",
        "/analytics/rules/devfeed-nohits-queries",
    ]
    assert requests[0][2]["params"]["destination_collection"].endswith("_search_queries")
    assert requests[1][2]["params"]["destination_collection"].endswith("_search_nohits")
    assert all(payload["collection"] == f"{engine.prefix}_articles" for _, _, payload in requests)


def test_setup_analytics_is_disabled_by_default(monkeypatch):
    from devfeed_core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "search_analytics_enabled", False)
    engine = Typesense.__new__(Typesense)
    monkeypatch.setattr(engine, "_create_analytics_collection", lambda _: pytest.fail("disabled"))
    engine.setup_analytics()
