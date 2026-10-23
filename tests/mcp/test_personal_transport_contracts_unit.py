"""Account transport routing and upstream failures preserve explicit OAuth authority."""

import asyncio
import json
from collections import deque

import httpx
import pytest
from devfeed_mcp import personal
from devfeed_mcp.config import Settings
from devfeed_mcp.personal import ACCOUNT_TOOLS, AccountToolAuth, UserAPI
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError


def access(*scopes):
    return AccessToken(token="test-account-access", client_id="client", scopes=list(scopes))


def request_api(monkeypatch, handler, *, token=None, **settings):
    monkeypatch.setattr(personal, "get_access_token", lambda: token)

    async def call(method="GET", path="v1/user/feed", *, scope="devfeed:read", **arguments):
        async with httpx.AsyncClient(
            base_url="https://account-api.example", transport=httpx.MockTransport(handler)
        ) as client:
            return await UserAPI(client, Settings(**settings)).request(
                method, path, scope=scope, **arguments
            )

    return call


@pytest.mark.parametrize("token", [None, access(), access("devfeed:write")])
def test_account_request_needs_the_requested_scope_before_contacting_upstream(monkeypatch, token):
    def forbidden(_):
        pytest.fail("An unauthorized request reached the account API")

    call = request_api(monkeypatch, forbidden, token=token)
    with pytest.raises(ToolError, match="devfeed:read permission"):
        asyncio.run(call())


def test_account_request_forwards_only_current_oauth_authority_and_preserves_query_values(
    monkeypatch,
):
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json={"items": [], "generation": "current"})

    call = request_api(monkeypatch, upstream, token=access("devfeed:read", "devfeed:write"))
    path = "v1/user/preferences/topics/00000000-0000-0000-0000-000000000001"
    result = asyncio.run(
        call("PUT", path, scope="devfeed:write", payload={"followed": False}, limit=1, cursor=None)
    )
    assert result == {"items": [], "generation": "current"}
    request = received[0]
    assert request.method == "PUT" and request.url.path == "/" + path
    assert request.headers["authorization"] == "Bearer test-account-access"
    assert dict(request.url.params) == {"limit": "1"}
    assert json.loads(request.content) == {"followed": False}
    assert "cookie" not in request.headers


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "authorization expired or was revoked"),
        (403, "does not have permission"),
        (404, "Record unavailable or parameters outdated"),
        (409, "Record unavailable or parameters outdated"),
        (422, "Record unavailable or parameters outdated"),
        (429, "temporarily unavailable"),
        (503, "temporarily unavailable"),
    ],
)
def test_account_http_failures_have_actionable_errors_without_exposing_upstream_body(
    monkeypatch, status, message
):
    call = request_api(
        monkeypatch,
        lambda _: httpx.Response(status, text="provider-private-details"),
        token=access("devfeed:read"),
    )
    with pytest.raises(ToolError, match=message) as raised:
        asyncio.run(call())
    assert "provider-private-details" not in str(raised.value)


@pytest.mark.parametrize("body", [b"not json", b"\xff", b'{"items":'])
def test_invalid_account_response_is_a_sanitized_tool_failure(monkeypatch, body):
    call = request_api(
        monkeypatch, lambda _: httpx.Response(200, content=body), token=access("devfeed:read")
    )
    with pytest.raises(ToolError, match="temporarily unavailable"):
        asyncio.run(call())


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, TimeoutError])
def test_account_transport_failures_hide_infrastructure_details(monkeypatch, failure):
    def upstream(_):
        raise failure("internal-host-and-provider-secret")

    call = request_api(monkeypatch, upstream, token=access("devfeed:read"))
    with pytest.raises(ToolError, match="temporarily unavailable") as raised:
        asyncio.run(call())
    assert "internal-host" not in str(raised.value) and raised.value.__suppress_context__


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("extra", [0, 1])
def test_streamed_account_response_obeys_the_limit_across_chunks_and_closes(monkeypatch, extra):
    body = json.dumps({"value": "x" * (1024 - len('{"value": ""}') + extra)}).encode()
    assert len(body) == 1024 + extra
    stream = Chunks(body[:600], body[600:])
    call = request_api(
        monkeypatch,
        lambda _: httpx.Response(200, stream=stream),
        token=access("devfeed:read"),
        max_response_bytes=1024,
    )
    if extra:
        with pytest.raises(ToolError, match="Response too large"):
            asyncio.run(call())
    else:
        assert len(asyncio.run(call())["value"]) == 1011
    assert stream.closed


def route(messages, *, method="POST", headers=()):
    pending = deque(messages)
    selected = []
    sent = []
    replayed = []

    async def receive():
        return pending.popleft() if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    def endpoint(name):
        async def app(scope, receive, send):
            selected.append(name)
            if scope["method"] == "POST":
                while True:
                    message = await receive()
                    replayed.append(message)
                    if not message.get("more_body", False):
                        break

        return app

    scope = {"type": "http", "method": method, "headers": list(headers), "path": "/mcp"}
    asyncio.run(AccountToolAuth(endpoint("public"), endpoint("account"))(scope, receive, send))
    return selected, replayed, sent, list(pending)


def message(body, more=False):
    return {"type": "http.request", "body": body, "more_body": more}


@pytest.mark.parametrize("name", sorted(ACCOUNT_TOOLS))
def test_each_account_tool_requires_authentication_without_an_authorization_header(name):
    body = json.dumps({"method": "tools/call", "params": {"name": name}}).encode()
    chunks = [message(body[:10], True), message(body[10:])]
    selected, replayed, sent, _ = route(chunks)
    assert selected == ["account"] and replayed == chunks and sent == []


@pytest.mark.parametrize(
    "payload",
    [
        {"method": "initialize"},
        {"method": "tools/list"},
        {"method": "tools/call", "params": {"name": "get_feed"}},
        {"method": "tools/call", "params": []},
        ["tools/call", "get_my_feed"],
    ],
)
def test_public_protocol_requests_retain_the_public_route_and_exact_body(payload):
    chunks = [message(json.dumps(payload).encode())]
    selected, replayed, _, _ = route(chunks)
    assert selected == ["public"] and replayed == chunks


@pytest.mark.parametrize("body", [b"not json", b"\xff", b""])
def test_malformed_protocol_body_is_replayed_to_the_sdk_for_validation(body):
    chunks = [message(body)]
    selected, replayed, _, _ = route(chunks)
    assert selected == ["public"] and replayed == chunks


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_discovery_requires_the_oauth_challenge_without_consuming_a_body(method):
    selected, replayed, sent, _ = route([], method=method)
    assert selected == ["account"] and replayed == sent == []


@pytest.mark.parametrize("header", [b"authorization", b"Authorization"])
def test_even_invalid_supplied_authority_cannot_fall_back_to_anonymous_access(header):
    selected, _, _, _ = route([message(b'{"method":"tools/list"}')], headers=[(header, b"")])
    assert selected == ["account"]


def test_disconnected_upload_never_enters_either_transport():
    selected, replayed, sent, _ = route([message(b"{", True), {"type": "http.disconnect"}])
    assert selected == replayed == sent == []


def test_oversized_fragmented_upload_is_rejected_before_reading_or_dispatching_the_remainder():
    remainder = message(b"not consumed")
    selected, replayed, sent, pending = route(
        [message(b"x" * 32768, True), message(b"x", True), remainder]
    )
    assert selected == replayed == [] and pending == [remainder]
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": "Request too large"}
