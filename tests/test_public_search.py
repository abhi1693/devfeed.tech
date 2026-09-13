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


def test_article_sort_and_date_filters_leave_catalogue_relevance_unchanged(monkeypatch):
    import json

    from devfeed_core.config import get_settings
    from pydantic import SecretStr

    settings = get_settings()
    settings.search_enabled, settings.search_url = True, "http://search:8108"
    settings.search_query_key = SecretStr("query-only")
    engine = Typesense()
    captured = []

    def request(*args, **kwargs):
        captured.append(kwargs["data"]["searches"])
        return json.dumps({"results": [{"hits": [], "found": 0}] * 4}).encode()

    monkeypatch.setattr(engine, "request", request)
    for order in ["newest", "oldest"]:
        engine.search("cloud", sort=order, date_from=100, date_to=200)
        article, *catalogue = captured[-1]
        assert (
            article["sort_by"]
            == f"published_at:{'desc' if order == 'newest' else 'asc'},_text_match:desc"
        )
        assert article["filter_by"] == "published_at:>=100 && published_at:<200"
        assert all("filter_by" not in query for query in catalogue)
        assert all(query["sort_by"] == "_text_match:desc,published_at:desc" for query in catalogue)


def test_search_rejects_invalid_sort_and_dates_before_index_access(monkeypatch):
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: SimpleNamespace()
    with TestClient(app) as client:
        for params in [
            {"sort": "popular"},
            {"date_from": "2026-02-30"},
            {"date_from": "2026-09-12", "date_to": "2026-09-01"},
            {"date_to": "9999-12-31"},
        ]:
            assert client.get("/v1/search", params={"q": "cloud", **params}).status_code == 422


def test_search_date_bounds_include_the_entire_end_day(monkeypatch):
    from datetime import UTC, datetime

    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    captured = {}

    class Engine:
        def search(self, query, kinds, page, **options):
            captured.update(options)
            return {kind: {"hits": [], "found": 0} for kind in kinds}

    monkeypatch.setattr("devfeed_api.search.Typesense", Engine)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(execute=lambda *a, **kw: None)
    with TestClient(app) as client:
        response = client.get(
            "/v1/search?q=cloud&section=articles&sort=newest&date_from=2026-09-01&date_to=2026-09-01"
        )
    assert response.status_code == 200
    assert captured == {
        "sort": "newest",
        "date_from": int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()),
        "date_to": int(datetime(2026, 9, 2, tzinfo=UTC).timestamp()),
    }


def test_search_rejects_future_date_bounds():
    from datetime import UTC, datetime, timedelta

    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: SimpleNamespace()
    with TestClient(app) as client:
        for key in ["date_from", "date_to"]:
            response = client.get("/v1/search", params={"q": "cloud", key: tomorrow})
            assert response.status_code == 422
            assert response.json()["detail"] == "Search dates must not be in the future"
