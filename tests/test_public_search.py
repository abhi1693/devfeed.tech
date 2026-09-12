from types import SimpleNamespace

import pytest
from devfeed_core.config import Settings
from devfeed_core.search_engine import SearchUnavailable, Typesense
from fastapi.testclient import TestClient


def test_search_is_bounded_and_does_not_fall_back_to_database(monkeypatch):
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    calls = []
    app = create_app()
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        execute=lambda *a, **kw: calls.append(a)
    )
    with TestClient(app) as client:
        for q in ["", "  ", "*", "?!"]:
            response = client.get("/v1/search", params={"q": q})
            assert response.status_code == 200
            assert all(not section["items"] for section in response.json()["sections"].values())
        assert client.get("/v1/search?q=python").status_code == 503
        assert client.get("/v1/search", params={"q": "x" * 201}).status_code == 422
        assert client.get("/v1/search?section=private&page=1").status_code == 422
        assert client.get("/v1/search?page=81").status_code == 422
        assert client.get("/v1/search", params={"q": "word " * 21}).status_code == 422
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "http://user:key@search:8108",
        "http://search?q=x",
        "file:///tmp/search",
        "https://search/api",
    ],
)
def test_search_service_configuration_rejects_unsafe_origins(url):
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://test", redis_url="redis://test", search_url=url)


def test_federated_request_is_server_controlled_and_only_returns_ids(monkeypatch):
    from devfeed_core.config import get_settings

    settings = get_settings()
    settings.search_enabled, settings.search_url = True, "http://search:8108"
    from pydantic import SecretStr

    settings.search_query_key = SecretStr("query-only")
    engine = Typesense()
    captured = []

    def request(*args, **kwargs):
        import json

        captured.append(kwargs["data"])
        return json.dumps({"results": [{"hits": [], "found": 0}] * 4}).encode()

    monkeypatch.setattr(engine, "request", request)
    engine.search("kuberentes network")
    searches = captured[0]["searches"]
    assert len(searches) == 4 and searches[0]["collection"].endswith("articles_v1")
    assert all(
        query["include_fields"] == "id" and query["drop_tokens_threshold"] == 0
        for query in searches
    )
    monkeypatch.setattr(
        engine, "request", lambda *a, **kw: b'{"results": [{"error": "private index detail"}]}'
    )
    with pytest.raises(SearchUnavailable):
        engine.search("python")


@pytest.mark.parametrize("payload", [b'{"success":false}', b"", b"not json"])
def test_import_rejects_partial_success_before_acknowledging_or_deleting(monkeypatch, payload):
    from devfeed_core.config import get_settings
    from pydantic import SecretStr

    settings = get_settings()
    settings.search_enabled, settings.search_url = True, "http://search:8108"
    settings.search_admin_key = SecretStr("index-only")
    engine = Typesense(admin=True)
    calls = []

    def request(method, *args, **kwargs):
        calls.append(method)
        return payload

    monkeypatch.setattr(engine, "request", request)
    with pytest.raises(SearchUnavailable):
        engine.sync("articles", [{"id": "article"}], {"deleted"})
    assert calls == ["POST"]
