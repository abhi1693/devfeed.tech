"""Exercise discovery through the actual MCP HTTP transport and public API boundary."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from devfeed_mcp.config import Settings
from devfeed_mcp.main import create_app
from jsonschema import validate
from pydantic import ValidationError
from starlette.testclient import TestClient

HEADERS = {"Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-11-25"}
ID = "00000000-0000-0000-0000-000000000001"
SOURCE = {
    "id": ID,
    "slug": "publisher",
    "name": "Publisher",
    "source_type": "publisher",
    "website_url": "https://publisher.example",
    "review_notes": "private",
}
TOPIC = {"id": ID, "slug": "python", "name": "Python", "kind": "language"}
ARTICLE = {
    "id": ID,
    "slug": "python-guide",
    "title": "Python guide",
    "canonical_url": "https://publisher.example/guide",
    "summary": "Publisher preview",
    "ai_summary": "AI preview",
    "published_at": None,
    "feed_at": "2026-09-01T12:00:00Z",
    "language": "en",
    "content_type": "tutorial",
    "topics": [TOPIC],
    "tags": ["python"],
    "sources": [SOURCE],
    "image_variants": [{"url": "https://images.example/large"}],
    "origins": [
        {
            "source_id": ID,
            "original_url": "https://publisher.example/guide",
            "source_metadata": {"discussion_url": "https://forum.example/1", "title": "RSS"},
        }
    ],
}


def rpc(client, method, params=None):
    response = client.post(
        "/mcp",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    assert "mcp-session-id" not in response.headers
    return response.json()


def call(client, name, **arguments):
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert "error" not in response, response
    return response["result"]


@pytest.fixture
def mcp_client():
    state = SimpleNamespace(requests=[], status=200, body=[], headers={})

    def handle(request):
        state.requests.append(request)
        return httpx.Response(state.status, json=state.body, headers=state.headers)

    app = create_app(
        Settings(api_url="https://public.example", allowed_origins=["https://client.example"]),
        transport=httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="http://localhost") as client:
        yield client, state


def test_mcp_initialize_and_discover_six_read_only_tools(mcp_client):
    client, state = mcp_client
    result = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "DevFeed"
    tools = rpc(client, "tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "search",
        "get_article",
        "get_feed",
        "list_topics",
        "list_sources",
        "get_source",
    }
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["outputSchema"]["type"] == "object"
    assert not state.requests


def test_article_projection_and_credentials_never_propagate(mcp_client):
    client, state = mcp_client
    state.body = ARTICLE
    client.headers.update({"Authorization": "Bearer private-token", "Cookie": "session=secret"})
    result = call(client, "get_article", article_id=ID)
    assert not result["isError"]
    article = result["structuredContent"]
    assert article["summary"] == "Publisher preview"
    assert article["ai_summary"] == "AI preview"
    assert article["published_at"] is None
    assert article["content_scope"] == "metadata_and_preview"
    assert article["origins"][0]["discussion_url"] == "https://forum.example/1"
    assert "source_metadata" not in article["origins"][0]
    assert "image_variants" not in article
    assert "review_notes" not in article["sources"][0]
    (request,) = state.requests
    assert request.method == "GET"
    assert str(request.url) == f"https://public.example/v1/articles/{ID}"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    schema = next(
        t for t in rpc(client, "tools/list")["result"]["tools"] if t["name"] == "get_article"
    )
    validate(article, schema["outputSchema"])


def test_feed_filters_and_cursor_are_preserved(mcp_client):
    client, state = mcp_client
    state.body = {"items": [ARTICLE], "next_cursor": "next+page=="}
    result = call(
        client,
        "get_feed",
        topic="python",
        source_id=ID,
        content_type="tutorial",
        languages=["en", "fr"],
        cursor="previous+page==",
        limit=5,
    )
    assert result["structuredContent"]["next_cursor"] == "next+page=="
    params = state.requests[0].url.params
    assert params.get_list("languages") == ["en", "fr"]
    assert params["cursor"] == "previous+page=="
    assert params["topic"] == "python" and params["source_id"] == ID
    assert params["content_type"] == "tutorial" and params["limit"] == "5"
    assert len(state.requests) == 1  # No engagement or publisher requests.


def test_search_filters_sections_and_pagination(mcp_client):
    client, state = mcp_client
    state.body = {
        "query": "python",
        "sections": {
            "articles": {
                "items": [
                    {
                        "id": ID,
                        "title": "Python guide",
                        "description": "Preview",
                        "href": "/articles/guide",
                        "label": "Publisher",
                        "published_at": None,
                        "image_url": "https://images.example/1",
                    }
                ],
                "next_cursor": "3",
            }
        },
    }
    result = call(
        client,
        "search",
        query="python",
        section="articles",
        page=2,
        topics=[ID],
        sources=[ID],
        tags=[ID],
        content_types=["news", "tutorial"],
        date_from="2020-01-01",
        date_to="2020-01-02",
        sort="newest",
    )
    section = result["structuredContent"]["sections"]["articles"]
    assert section["next_cursor"] == "3"
    assert "image_url" not in section["items"][0]
    params = state.requests[0].url.params
    assert params["q"] == "python" and params["page"] == "2"
    assert params.get_list("content_types") == ["news", "tutorial"]
    assert params["date_from"] == "2020-01-01"
    assert all(params[key] == ID for key in ("topics", "sources", "tags"))


@pytest.mark.parametrize(
    "name,row,path",
    [
        ("list_topics", TOPIC, "topics"),
        ("list_sources", SOURCE, "sources"),
    ],
)
def test_catalogue_lookahead_pagination(mcp_client, name, row, path):
    client, state = mcp_client
    state.body = [row, row, row]
    result = call(client, name, query="py", offset=4, limit=2, languages=["en"], has_articles=True)
    assert len(result["structuredContent"]["items"]) == 2
    assert result["structuredContent"]["next_offset"] == 6
    assert state.requests[0].url.path == f"/v1/{path}"
    assert state.requests[0].url.params["limit"] == "3"
    state.body = [row, row]
    assert call(client, name, limit=2)["structuredContent"]["next_offset"] is None


def test_source_profile_and_source_filters(mcp_client):
    client, state = mcp_client
    state.body = [SOURCE]
    call(client, "list_sources", enabled=False, source_type="publisher")
    assert state.requests[-1].url.params["enabled"] == "false"
    assert state.requests[-1].url.params["source_type"] == "publisher"
    state.body = SOURCE
    result = call(client, "get_source", source_id="publisher")
    assert result["structuredContent"]["website_url"] == "https://publisher.example"
    assert "review_notes" not in result["structuredContent"]


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("get_feed", {"limit": 0}),
        ("get_feed", {"limit": 51}),
        ("get_feed", {"source_id": "not-a-uuid"}),
        ("get_feed", {"languages": ["en-US"]}),
        ("get_article", {"article_id": "../../v1/admin/sources"}),
        ("get_article", {"article_id": "%2e%2e%2fadmin"}),
        ("get_source", {"source_id": "https://evil.example"}),
        ("get_source", {"source_id": "publisher?secret=yes"}),
        ("search", {"query": "x" * 201}),
        ("search", {"query": "python", "page": 81}),
        ("search", {"query": "python", "topics": [ID] * 21}),
        ("search", {"query": "python", "date_from": "2020-02-02", "date_to": "2020-01-01"}),
        (
            "search",
            {
                "query": "python",
                "date_to": (datetime.now(UTC) + timedelta(days=1)).date().isoformat(),
            },
        ),
        ("list_topics", {"offset": -1}),
        ("list_sources", {"limit": 1000}),
    ],
)
def test_invalid_arguments_cannot_reach_upstream(mcp_client, name, arguments):
    client, state = mcp_client
    assert call(client, name, **arguments)["isError"]
    assert not state.requests


@pytest.mark.parametrize("status", [302, 404, 422, 429, 500, 503])
def test_upstream_errors_are_safe_and_redirects_are_not_followed(mcp_client, status):
    client, state = mcp_client
    state.status = status
    state.body = {"detail": "private database credentials"}
    state.headers = {"Location": "http://private.example/admin"}
    result = call(client, "get_article", article_id=ID)
    assert result["isError"]
    assert "credentials" not in json.dumps(result)
    assert len(state.requests) == 1


@pytest.mark.parametrize("kind", ["timeout", "connection", "invalid_json", "large", "slow"])
def test_bounded_upstream_failures(kind):
    async def handle(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("private host")
        if kind == "connection":
            raise httpx.ConnectError("private host")
        if kind == "slow":
            await asyncio.sleep(1)
        return httpx.Response(200, content=b"x" * (2048 if kind == "large" else 1))

    app = create_app(
        Settings(max_response_bytes=1024, timeout_seconds=0.05),
        transport=httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="http://localhost") as client:
        result = call(client, "get_source", source_id=ID)
        assert result["isError"]
        assert "private host" not in json.dumps(result)


def test_health_and_transport_security(mcp_client):
    client, state = mcp_client
    assert client.get("/health/live").status_code == 200
    assert not state.requests
    assert client.get("/health/ready").status_code == 200
    state.status = 503
    assert client.get("/health/ready").status_code == 503
    assert client.get("/version").json()["version"]
    assert client.post("/mcp", headers={"Host": "attacker.example"}, json={}).status_code == 421
    assert (
        client.post("/mcp", headers={"Origin": "https://attacker.example"}, json={}).status_code
        == 403
    )
    response = client.options(
        "/mcp",
        headers={
            "Origin": "https://client.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "content-type,mcp-protocol-version,mcp-method,mcp-name"
            ),
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.example"


def test_request_body_limit(mcp_client):
    client, state = mcp_client
    response = client.post("/mcp", headers=HEADERS, json={"padding": "x" * 40_000})
    assert response.status_code == 413
    assert not state.requests


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:secret@example.com",
        "http://example.com/admin",
        "http://example.com/api",
        "http://example.com?url=other",
        "http://example.com/#fragment",
        "http://example.com:bad",
    ],
)
def test_configuration_rejects_credentials_and_arbitrary_paths(url):
    with pytest.raises(ValidationError):
        Settings(api_url=url)


def test_tools_use_real_public_routes_and_serialization():
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app as create_api
    from test_publication_api import record

    article = record(slug="python-guide")
    article.origins[0].source.slug = "publisher"
    public = create_api()
    session = SimpleNamespace(scalar=lambda _: article)
    public.dependency_overrides[get_session] = lambda: session
    app = create_app(Settings(), transport=httpx.ASGITransport(public))
    with TestClient(app, base_url="http://localhost") as client:
        result = call(client, "get_article", article_id=str(article.id))
        assert not result["isError"]
        assert result["structuredContent"]["canonical_url"] == article.canonical_url
        session.scalar = lambda _: None
        assert call(client, "get_article", article_id=str(article.id))["isError"]
