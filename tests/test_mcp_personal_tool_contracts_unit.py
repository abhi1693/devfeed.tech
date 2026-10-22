"""Account tools use fixed owned routes, explicit writes and safe typed projections.

The OAuth middleware's verified access context is the input boundary. These units
exercise the real SDK tool registry and HTTP client, not grant/Redis verification.
"""

import asyncio
import json
import time
import uuid
from unittest.mock import Mock

import httpx
import pytest
from devfeed_mcp import personal
from devfeed_mcp.config import Settings
from devfeed_mcp.transport import BrowserNegotiation
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from test_mcp import ARTICLE

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
GENERATION = str(uuid.uuid4())


CASES = [
    (
        "get_my_feed",
        {"limit": 2, "cursor": "issued-cursor", "generation": GENERATION, "sort": "newest"},
        "GET",
        "/v1/user/feed",
        {
            "items": [ARTICLE],
            "next_cursor": "next-issued-cursor",
            "generation": GENERATION,
            "refreshing": False,
            "feed_kind": "personalized",
        },
    ),
    (
        "get_my_must_reads",
        {"timezone": "Asia/Kolkata"},
        "GET",
        "/v1/user/must-reads",
        {
            "date": "2026-10-23",
            "timezone": "Asia/Kolkata",
            "items": [ARTICLE],
            "reasons": {IDENTIFIER: "followed_topic"},
            "read_ids": [],
            "preparing": False,
        },
    ),
    (
        "list_my_bookmarks",
        {"limit": 2, "cursor": "issued-cursor"},
        "GET",
        "/v1/user/bookmarks",
        {"items": [ARTICLE], "next_cursor": None},
    ),
    ("list_my_followed_topics", {}, "GET", "/v1/user/preferences", {"topic_ids": [IDENTIFIER]}),
    (
        "list_my_followed_sources",
        {},
        "GET",
        "/v1/user/preferences/sources",
        {"source_ids": [IDENTIFIER]},
    ),
    (
        "set_bookmark",
        {"article_id": IDENTIFIER, "bookmarked": True},
        "PUT",
        f"/v1/user/articles/{IDENTIFIER}/bookmark",
        {"article_id": IDENTIFIER, "bookmarked": True},
    ),
    (
        "set_topic_follow",
        {"topic_id": IDENTIFIER, "followed": False},
        "PUT",
        f"/v1/user/preferences/topics/{IDENTIFIER}",
        {"followed": False},
    ),
    (
        "set_source_follow",
        {"source_id": IDENTIFIER, "followed": True},
        "PUT",
        f"/v1/user/preferences/sources/{IDENTIFIER}",
        {"followed": True},
    ),
    (
        "set_article_like",
        {"article_id": IDENTIFIER, "liked": True},
        "PUT",
        f"/v1/user/articles/{IDENTIFIER}/like",
        {"article_id": IDENTIFIER, "liked": True, "likes": 1, "bookmarked": False},
    ),
]


@pytest.mark.parametrize("name,arguments,method,path,document", CASES)
def test_personal_tool_preserves_owned_route_explicit_intent_and_response_schema(
    monkeypatch, name, arguments, method, path, document
):
    async def run():
        token = AccessToken(
            token="verified-context-token",
            client_id="registered-agent",
            scopes=["devfeed:read", "devfeed:write"],
            expires_at=int(time.time()) + 300,
            resource="https://mcp.example/mcp",
            subject=IDENTIFIER,
        )
        monkeypatch.setattr(personal, "get_access_token", lambda: token)
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={**document, "private_session": "must-not-project"})

        async with httpx.AsyncClient(
            base_url="https://user.example/", transport=httpx.MockTransport(respond)
        ) as client:
            server = MCPServer("Unit account tools", subscriptions=False)
            personal.register_personal_tools(server, personal.UserAPI(client, Settings()))
            output = await server.call_tool(name, arguments)
        assert not output.is_error and output.structured_content is not None
        (request,) = requests
        assert (request.method, request.url.host, request.url.path) == (
            method,
            "user.example",
            path,
        )
        assert (
            request.headers["authorization"] == "Bearer verified-context-token"
            and "cookie" not in request.headers
        )
        assert "user_id" not in request.url.params and "subject" not in request.url.params
        if method == "PUT":
            field = (
                "bookmarked"
                if name == "set_bookmark"
                else "liked"
                if name == "set_article_like"
                else "followed"
            )
            assert json.loads(request.content) == {field: arguments[field]}
            assert output.structured_content[field] is arguments[field]
        else:
            assert dict(request.url.params) == {key: str(value) for key, value in arguments.items()}
        if "items" in document:
            (article,) = output.structured_content["items"]
            assert (
                article["id"] == IDENTIFIER and article["content_scope"] == "metadata_and_preview"
            )
            assert "image_variants" not in article and "review_notes" not in article["sources"][0]
        if name == "get_my_feed":
            assert output.structured_content["generation"] == GENERATION
            assert output.structured_content["next_cursor"] == "next-issued-cursor"
        assert "must-not-project" not in output.model_dump_json()

    asyncio.run(run())


@pytest.mark.parametrize(
    "name,args",
    [
        ("set_bookmark", {"article_id": IDENTIFIER, "bookmarked": True}),
        ("set_article_like", {"article_id": IDENTIFIER, "liked": False}),
    ],
)
def test_read_only_context_cannot_turn_an_explicit_write_tool_into_an_owned_api_request(
    monkeypatch, name, args
):
    async def run():
        token = AccessToken(
            token="verified-read-token",
            client_id="registered-agent",
            scopes=["devfeed:read"],
            expires_at=int(time.time()) + 300,
        )
        monkeypatch.setattr(personal, "get_access_token", lambda: token)
        transport = Mock(side_effect=AssertionError("Write reached owned API without permission"))
        async with httpx.AsyncClient(
            base_url="https://user.example/", transport=httpx.MockTransport(transport)
        ) as client:
            server = MCPServer("Unit account tools", subscriptions=False)
            personal.register_personal_tools(server, personal.UserAPI(client, Settings()))
            with pytest.raises(ToolError, match="devfeed:write permission"):
                await server.call_tool(name, args)
        transport.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize(
    "accept,expected", [("text/html;charset=utf-8", True), ("text/html;q=0;charset=utf-8", False)]
)
def test_browser_negotiation_does_not_treat_an_unrelated_media_parameter_as_quality(
    accept, expected
):
    request = Request({"type": "http", "headers": [(b"accept", accept.encode())]})
    assert BrowserNegotiation.wants_instructions(request) is expected
