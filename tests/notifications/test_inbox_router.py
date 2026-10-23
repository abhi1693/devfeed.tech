"""Explicit subscriber inbox routes preserve protocol bounds and transport ownership."""

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import anyio
import httpx
import pytest
from devfeed_http.inbox import (
    INBOX_OPERATIONS,
    INBOX_POLICY_EXTENSION,
    InboxProxy,
    InboxRouter,
    InboxSession,
)
from devfeed_http.operation_policies import (
    GATEWAY_ROUTES_EXTENSION,
    GatewayPolicy,
    export_gateway_document,
    include_gateway_router,
)
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.requests import ClientDisconnect

BASE = "/renamed/chimely/v1/inbox"
NOTIFICATION = "notif_" + "a" * 26
BROADCAST = "bcast_" + "b" * 26
PARAMETERS = {"notification_id": NOTIFICATION, "broadcast_id": BROADCAST}
EXPECTED = {
    ("GET", "/items"),
    ("GET", "/counts"),
    ("GET", "/preferences"),
    ("PUT", "/preferences"),
    ("GET", "/stream"),
    ("POST", "/read-all"),
    ("POST", "/seen-all"),
    ("POST", "/archive-all"),
    ("POST", "/archive-read"),
    *{
        ("POST", f"/{collection}/{{{parameter}}}/{action}")
        for collection, parameter in (
            ("notifications", "notification_id"),
            ("broadcasts", "broadcast_id"),
        )
        for action in ("read", "unread", "archive", "unarchive")
    },
}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, failure=None, stalled=False):
        self.chunks, self.failure, self.stalled = chunks, failure, stalled
        self.closes = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.stalled:
            await asyncio.Event().wait()
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closes += 1


class Client(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closes = 0

    async def aclose(self):
        self.closes += 1
        await super().aclose()


@pytest.fixture
def gateway():
    state = SimpleNamespace(
        requests=[],
        clients=[],
        upstream=[],
        streams=[],
        auth=[],
        status=200,
        headers={},
        chunks=None,
        failure=None,
        stalled=False,
        send_failure=None,
        auth_status=None,
    )

    def upstream(request):
        state.requests.append(request)
        if state.send_failure is not None:
            raise state.send_failure
        events = request.url.path.endswith("/stream")
        chunks = state.chunks
        if chunks is None:
            chunks = [b"event: hint\ndata: {}\n\n" if events else b'{"items":[]}']
        stream = Chunks(chunks, failure=state.failure, stalled=state.stalled)
        response = httpx.Response(
            state.status,
            headers={
                "Content-Type": "text/event-stream" if events else "application/json",
                **state.headers,
            },
            stream=stream,
        )
        state.streams.append(stream)
        state.upstream.append(response)
        return response

    def client():
        value = Client(transport=httpx.MockTransport(upstream))
        state.clients.append(value)
        return value

    state.session = InboxSession(
        api_url="https://inbox.test/",
        environment="private-users",
        secret="private-secret",
        subscriber_id="user_authenticated",
        expires_at=int(time.time()) + 60,
        client_factory=client,
    )

    def authorized():
        state.auth.append(True)
        if state.auth_status is not None:
            raise HTTPException(state.auth_status, "Browser session required")
        return state.session

    state.router = InboxRouter(
        session_dependency=authorized,
        operation_prefix="unitInbox",
        gateway_policy=GatewayPolicy(),
    )
    state.app = FastAPI()
    include_gateway_router(state.app, state.router)
    # A second inclusion proves policy is bound to the operation, not one prefix.
    state.app.include_router(state.router, prefix="/renamed")
    return state


def request(*, method="GET", query=b"", headers=(), chunks=(), receive=None):
    messages = iter(chunks)

    async def next_message():
        return next(messages, {"type": "http.request", "body": b"", "more_body": False})

    return Request(
        {"type": "http", "method": method, "headers": list(headers), "query_string": query},
        receive or next_message,
    )


def body(chunk, *, more=False):
    return {"type": "http.request", "body": chunk, "more_body": more}


def operation(name):
    return next(item for item in INBOX_OPERATIONS if item.name == name)


async def exchange(
    app, path, *, method="GET", headers=(), chunks=(), root_path="", forbid_read=False
):
    messages, events, reads = iter(chunks), [], []

    async def receive():
        if forbid_read:
            pytest.fail("Request body read before native routing or authentication")
        reads.append(True)
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        events.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": "https",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": list(headers),
        "server": ("service.test", 443),
        "client": ("127.0.0.1", 1234),
    }
    await app(scope, receive, send)
    return events, reads


def status(events):
    return next(item["status"] for item in events if item["type"] == "http.response.start")


def test_factory_registers_exact_protocol_without_catchall_and_exports_typed_item_bounds(gateway):
    document = export_gateway_document(gateway.app)
    routes = [
        item
        for item in document[GATEWAY_ROUTES_EXTENSION]
        if item["operationId"].startswith("unitInbox")
    ]
    assert len(routes) == 34  # One original and one prefixed inclusion of all seventeen.
    originals = [item for item in routes if not item["path"].startswith("/renamed/")]
    assert len(originals) == 17
    assert {(item.method, item.path) for item in INBOX_OPERATIONS} == EXPECTED
    assert len({item.name for item in INBOX_OPERATIONS}) == 17
    assert all("{path}" not in item["path"] for item in routes)
    assert all(item["policy"]["bodyLimit"] == 16_384 for item in routes)
    assert all(item["policy"]["bodyTimeoutMs"] == 2000 for item in routes)
    for item in originals:
        for parameter in item["parameters"]:
            assert parameter["converter"] == "str"
            assert parameter["schema"]["minLength"] == parameter["schema"]["maxLength"] == 32
            prefix = "notif" if parameter["name"] == "notification_id" else "bcast"
            assert parameter["schema"]["pattern"] == rf"^{prefix}_[a-z0-9]{{26}}$"
    for context in iter_route_contexts(gateway.app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        declared = context.openapi_extra[INBOX_POLICY_EXTENSION]
        assert declared["responseMode"] == (
            "events" if context.operation_id == "unitInboxStream" else "json"
        )
    assert document["paths"] == {}


@pytest.mark.parametrize("declared", INBOX_OPERATIONS, ids=lambda item: item.name)
def test_every_registered_operation_forwards_only_its_declared_upstream(gateway, declared):
    async def exercise():
        path = declared.path.format(**PARAMETERS)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(gateway.app), base_url="https://service.test"
        ) as client:
            response = await client.request(declared.method, BASE + path)
        assert response.status_code == 200
        expected = declared.upstream_path.format(**PARAMETERS)
        assert gateway.requests[0].url.path == expected
        assert gateway.requests[0].method == declared.method
        assert gateway.requests[0].headers["x-chimely-environment"] == "private-users"
        assert gateway.requests[0].headers["x-chimely-subscriber"] == "user_authenticated"
        assert gateway.clients[0].is_closed and gateway.clients[0].closes == 1
        assert gateway.upstream[0].is_closed and gateway.streams[0].closes == 1
        if declared.response_mode == "events":
            assert response.content.startswith(b": connected\n\n")
            assert response.headers["x-accel-buffering"] == "no"
        else:
            assert response.json() == {"items": []}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("DELETE", "/items", 405),
        ("POST", "/counts", 405),
        ("GET", "/read-all", 405),
        ("GET", "/management", 404),
        ("POST", "/notifications/" + NOTIFICATION + "/delete", 404),
        ("POST", "/notifications/bcast_" + "a" * 26 + "/read", 422),
        ("POST", "/broadcasts/notif_" + "a" * 26 + "/read", 422),
        ("POST", "/notifications/notif_" + "a" * 25 + "/read", 422),
        ("POST", "/notifications/notif_" + "A" * 26 + "/read", 422),
    ],
)
def test_native_routing_and_typed_ids_reject_unsupported_calls_without_reading_body(
    gateway, method, path, expected
):
    events, reads = asyncio.run(
        exchange(
            gateway.app,
            BASE + path,
            method=method,
            headers=[(b"content-length", b"999999")],
            forbid_read=True,
        )
    )
    assert status(events) == expected and not reads
    assert not gateway.requests and not gateway.clients
    if expected in {404, 405}:
        assert not gateway.auth


@pytest.mark.parametrize("root_path", ["", "/edge"])
def test_nested_mounted_router_retains_policy_and_upstream_without_raw_path_checks(
    gateway, root_path
):
    child = FastAPI()
    parent = APIRouter(prefix="/workspace")
    parent.include_router(gateway.router, prefix="/another")
    child.include_router(parent, prefix="/v9")
    app = FastAPI()
    app.mount("/proxy", child)
    path = root_path + "/proxy/v9/workspace/another/chimely/v1/inbox/items"
    events, reads = asyncio.run(exchange(app, path, root_path=root_path, chunks=[body(b"")]))
    assert status(events) == 200 and reads == [True]
    assert gateway.requests[0].url.path == "/v1/inbox/items"
    assert gateway.clients[0].is_closed


def test_session_dependency_precedes_body_read_and_client_allocation(gateway):
    gateway.auth_status = 401
    events, reads = asyncio.run(
        exchange(
            gateway.app,
            BASE + "/preferences",
            method="PUT",
            headers=[(b"content-length", b"999999")],
            forbid_read=True,
        )
    )
    assert status(events) == 401 and not reads and gateway.auth == [True]
    assert not gateway.requests and not gateway.clients
    gateway.auth_status = None
    events, reads = asyncio.run(
        exchange(
            gateway.app,
            BASE + "/preferences",
            method="PUT",
            headers=[(b"content-length", b"999999")],
            forbid_read=True,
        )
    )
    assert status(events) == 413 and not reads and gateway.auth == [True, True]
    assert not gateway.requests and not gateway.clients


def test_subscriber_credentials_ignore_forged_identity_and_preserve_only_bounded_hints(gateway):
    async def exercise():
        incoming = request(
            query=b"environment=other&subscriber_id=foreign&limit=10&cursor=next&filter=archived&last_event_id=resume",
            headers=[
                (b"authorization", b"Bearer forged"),
                (b"x-chimely-subscriber", b"victim"),
                (b"x-chimely-subscriber-hash", b"forged"),
                (b"cookie", b"private-browser-cookie"),
                (b"if-none-match", b'"current"'),
                (b"last-event-id", b"older"),
            ],
        )
        result = await InboxProxy().forward(operation("Items"), incoming, gateway.session)
        assert result.status_code == 200
        sent = gateway.requests[0]
        assert dict(sent.url.params) == {"limit": "10", "cursor": "next", "filter": "archived"}
        assert sent.headers["last-event-id"] == "resume"
        assert sent.headers["if-none-match"] == '"current"'
        expected = hmac.new(b"private-secret", b"user_authenticated", hashlib.sha256).hexdigest()
        assert sent.headers["x-chimely-subscriber-hash"] == expected
        assert "authorization" not in sent.headers and "cookie" not in sent.headers
        assert "private-secret" not in repr(gateway.session)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "query,headers",
    [
        (b"limit=1&limit=2", ()),
        (b"cursor=" + b"x" * 2049, ()),
        (b"", ((b"if-none-match", b"x" * 2049),)),
        (b"", ((b"last-event-id", b"x" * 2049),)),
    ],
)
def test_invalid_hints_fail_before_upstream_or_client_allocation(gateway, query, headers):
    async def exercise():
        with pytest.raises(HTTPException) as caught:
            await InboxProxy().forward(
                operation("Items"), request(query=query, headers=headers), gateway.session
            )
        assert caught.value.status_code == 422
        assert not gateway.clients and not gateway.requests

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("declared", 413),
        ("chunked", 413),
        ("malformed-json", 422),
        ("stalled", 408),
        ("disconnect", None),
    ],
)
def test_body_validation_and_deadline_precede_transport_allocation(gateway, kind, expected):
    async def exercise():
        async def stalled():
            await asyncio.Event().wait()

        declared = operation("PreferencesUpdate").model_copy(
            update={"body_limit": 4, "body_timeout_ms": 1}
        )
        incoming = request(
            method="PUT",
            headers=[(b"content-length", b"5")] if kind == "declared" else (),
            chunks=[body(b"abc", more=True), body(b"de")]
            if kind == "chunked"
            else [body(b"oops")]
            if kind == "malformed-json"
            else [{"type": "http.disconnect"}]
            if kind == "disconnect"
            else (),
            receive=stalled if kind == "stalled" else None,
        )
        error = ClientDisconnect if expected is None else HTTPException
        with pytest.raises(error) as caught:
            await InboxProxy().forward(declared, incoming, gateway.session)
        if expected is not None:
            assert caught.value.status_code == expected
        assert not gateway.clients and not gateway.requests

    asyncio.run(exercise())


def test_exact_multibyte_byte_limit_forwards_json_once(gateway):
    async def exercise():
        encoded = '"é"'.encode()
        declared = operation("PreferencesUpdate").model_copy(update={"body_limit": len(encoded)})
        incoming = request(method="PUT", chunks=[body(encoded[:2], more=True), body(encoded[2:])])
        result = await InboxProxy().forward(declared, incoming, gateway.session)
        assert result.status_code == 200
        assert gateway.requests[0].content == encoded
        assert gateway.requests[0].headers["content-type"] == "application/json"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "upstream,expected",
    [
        (400, 400),
        (404, 404),
        (409, 409),
        (422, 422),
        (429, 429),
        (301, 503),
        (401, 503),
        (403, 503),
        (500, 503),
    ],
)
def test_upstream_status_errors_are_sanitized_and_close_transport(gateway, upstream, expected):
    gateway.status, gateway.chunks = upstream, [b"private upstream failure"]

    async def exercise():
        result = await InboxProxy().forward(operation("Items"), request(), gateway.session)
        assert result.status_code == expected
        assert b"private upstream" not in result.body
        assert result.headers["cache-control"] == "no-store"
        assert gateway.clients[0].is_closed and gateway.clients[0].closes == 1
        assert gateway.upstream[0].is_closed and gateway.streams[0].closes == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["send", "read", "oversize", "deadline"])
def test_response_failures_and_bounds_close_every_transport(gateway, kind):
    if kind == "send":
        gateway.send_failure = httpx.ConnectError("private transport error")
    elif kind == "read":
        gateway.failure = httpx.ReadError("private transport error")
    elif kind == "oversize":
        gateway.chunks = [b"abc", b"def"]
    else:
        gateway.chunks, gateway.stalled = [], True

    async def exercise():
        declared = operation("Items").model_copy(
            update={"response_limit": 4, "upstream_timeout_ms": 1 if kind == "deadline" else 1000}
        )
        result = await InboxProxy().forward(declared, request(), gateway.session)
        assert result.status_code == 503 and b"private" not in result.body
        assert gateway.clients[0].is_closed and gateway.clients[0].closes == 1
        assert all(response.is_closed for response in gateway.upstream)
        assert all(stream.closes == 1 for stream in gateway.streams)

    asyncio.run(exercise())


def test_etag_not_modified_preserves_headers_and_closes_without_body_read(gateway):
    gateway.status, gateway.headers = 304, {"ETag": '"stable"'}

    async def exercise():
        result = await InboxProxy().forward(operation("Items"), request(), gateway.session)
        assert result.status_code == 304 and not result.body
        assert result.headers["etag"] == '"stable"'
        assert gateway.clients[0].is_closed and gateway.upstream[0].is_closed

    asyncio.run(exercise())


@pytest.mark.parametrize("response_mode", ["json", "events"])
def test_response_mode_is_declared_independently_of_upstream_path_suffix(gateway, response_mode):
    gateway.headers = {
        "Content-Type": "text/event-stream" if response_mode == "events" else "application/json"
    }
    gateway.chunks = [
        b"event: hint\ndata: {}\n\n" if response_mode == "events" else b'{"items":[]}'
    ]

    async def exercise():
        declared = operation("Items").model_copy(
            update={
                "upstream_path": "/v1/inbox/stream"
                if response_mode == "json"
                else "/v1/inbox/renamed-events",
                "response_mode": response_mode,
            }
        )
        result = await InboxProxy().forward(declared, request(), gateway.session)
        if response_mode == "json":
            assert result.media_type == "application/json" and json.loads(result.body) == {
                "items": []
            }
        else:
            events = []

            async def send(message):
                events.append(message)

            await result({"type": "http", "asgi": {"spec_version": "2.4"}}, request().receive, send)
            assert result.media_type == "text/event-stream"
            assert events[1]["body"] == b": connected\n\n"
        assert gateway.clients[0].is_closed

    asyncio.run(exercise())


@pytest.mark.parametrize("disconnect_at", ["start", "first-body"])
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_stream_response_owns_cleanup_when_send_fails_before_or_after_first_chunk(
    gateway, disconnect_at, spec_version
):
    async def exercise():
        result = await InboxProxy().forward(operation("Stream"), request(), gateway.session)

        async def send(message):
            if message["type"] == (
                "http.response.start" if disconnect_at == "start" else "http.response.body"
            ):
                raise OSError("Client disconnected")

        async def receive():
            await asyncio.Event().wait()

        with anyio.CancelScope():
            with pytest.raises(ClientDisconnect if spec_version == "2.4" else OSError):
                await result(
                    {"type": "http", "asgi": {"spec_version": spec_version}}, receive, send
                )
        assert gateway.clients[0].is_closed and gateway.clients[0].closes == 1
        assert gateway.upstream[0].is_closed and gateway.streams[0].closes == 1
        # Closing an unstarted/suspended iterator must remain safe in the caller task.
        await result.body_iterator.aclose()

    asyncio.run(exercise())


def test_asgi_disconnect_during_first_frame_unwinds_the_streaming_task_deadline(gateway):
    async def exercise():
        result = await InboxProxy().forward(operation("Stream"), request(), gateway.session)
        first_body = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body":
                first_body.set()
                await asyncio.Event().wait()

        async def receive():
            await first_body.wait()
            return {"type": "http.disconnect"}

        await result({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
        assert gateway.clients[0].is_closed and gateway.clients[0].closes == 1
        assert gateway.upstream[0].is_closed and gateway.streams[0].closes == 1
        await result.body_iterator.aclose()

    asyncio.run(exercise())


def test_events_require_an_event_stream_response_and_close_a_mismatched_transport(gateway):
    gateway.headers = {"Content-Type": "application/json"}

    async def exercise():
        result = await InboxProxy().forward(operation("Stream"), request(), gateway.session)
        assert result.status_code == 503
        assert gateway.clients[0].is_closed and gateway.clients[0].closes == 1
        assert gateway.upstream[0].is_closed and gateway.streams[0].closes == 1

    asyncio.run(exercise())


def test_session_expiry_bounds_a_stalled_event_stream_and_closes_transport(gateway):
    gateway.chunks, gateway.stalled = [], True

    async def exercise():
        session = replace(gateway.session, expires_at=int(time.time()) - 1)
        result = await InboxProxy().forward(operation("Stream"), request(), session)
        events = []

        async def send(message):
            events.append(message)

        await result({"type": "http", "asgi": {"spec_version": "2.4"}}, request().receive, send)
        assert events[1]["body"] == b": connected\n\n"
        assert gateway.clients[0].is_closed and gateway.streams[0].closes == 1

    asyncio.run(exercise())
