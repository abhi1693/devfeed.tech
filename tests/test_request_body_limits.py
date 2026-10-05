"""Exercise byte accounting at the ASGI boundary, before application parsing."""

import asyncio

import pytest
from devfeed_http.body_limits import RequestBodyLimitMiddleware


def exchange(chunks, *, headers=(), path="/click", method="POST", scope_type="http", root_path=""):
    events = []
    calls = []
    received = []
    messages = iter(chunks)

    async def receive():
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        events.append(message)

    async def app(scope, receive, send):
        calls.append(scope)
        received.append(await receive())
        received.append(await receive())

    middleware = RequestBodyLimitMiddleware(app, limits={("POST", "/click"): 4})
    scope = {
        "type": scope_type,
        "path": path,
        "method": method,
        "headers": headers,
        "root_path": root_path,
    }
    asyncio.run(middleware(scope, receive, send))
    return calls, received, events


@pytest.mark.parametrize(
    "header", [[], [(b"content-length", b"0")], [(b"content-length", b"wrong")]]
)
def test_actual_chunked_bytes_are_authoritative(header):
    calls, received, events = exchange(
        [
            {"type": "http.request", "body": "é".encode(), "more_body": True},
            {"type": "http.request", "body": "éx".encode(), "more_body": False},
        ],
        headers=header,
    )
    assert not calls and not received
    assert events[0]["status"] == 413
    assert (b"cache-control", b"no-store") in events[0]["headers"]


def test_exact_limit_is_replayed_once_and_disconnect_remains_visible():
    calls, received, events = exchange(
        [
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": False},
        ]
    )
    assert len(calls) == 1 and not events
    assert received == [
        {"type": "http.request", "body": b"abcd", "more_body": False},
        {"type": "http.disconnect"},
    ]


def test_declared_oversize_is_rejected_without_reading():
    calls, received, events = exchange([], headers=[(b"content-length", b"5")])
    assert not calls and not received and events[0]["status"] == 413


def test_peer_disconnection_never_runs_application():
    assert exchange([{"type": "http.disconnect"}]) == ([], [], [])


def test_mounted_root_path_cannot_bypass_limit():
    calls, received, events = exchange(
        [{"type": "http.request", "body": b"oversize", "more_body": False}],
        path="/proxy/click",
        root_path="/proxy",
    )
    assert not calls and not received and events[0]["status"] == 413


def test_incomplete_body_releases_request_without_running_application():
    events = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        events.append(message)

    async def app(*args):
        pytest.fail("Incomplete body reached application")

    middleware = RequestBodyLimitMiddleware(app, limits={("POST", "/click"): 4}, timeout=0.01)
    asyncio.run(middleware({"type": "http", "method": "POST", "path": "/click"}, receive, send))
    assert events[0]["status"] == 408
    assert (b"cache-control", b"no-store") in events[0]["headers"]


@pytest.mark.parametrize(
    "options", [{"method": "GET"}, {"path": "/other"}, {"scope_type": "websocket"}]
)
def test_other_routes_and_protocols_are_unchanged(options):
    message = {"type": "http.request", "body": b"large data", "more_body": False}
    calls, received, events = exchange([message], **options)
    assert len(calls) == 1 and received[0] == message and not events
