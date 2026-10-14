"""Exercise fixed endpoint byte policies before application parsing."""

import asyncio
import json

import pytest
from devfeed_http.body_limits import BoundedRequestBody, RequestBodyPolicy


def exchange(chunks, *, headers=(), scope_type="http", rejection="json"):
    events, calls, received = [], [], []
    messages = iter(chunks)

    async def receive():
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        events.append(message)

    async def app(scope, receive, send):
        calls.append(scope)
        received.append(await receive())
        received.append(await receive())

    guard = BoundedRequestBody(app, policy=RequestBodyPolicy(body_limit=4, rejection=rejection))
    asyncio.run(guard({"type": scope_type, "headers": headers}, receive, send))
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
    assert json.loads(events[1]["body"]) == {"detail": "Request too large"}


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


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"5")],
        [(b"content-length", b"1"), (b"content-length", b"5")],
        [(b"content-length", b"5"), (b"content-length", b"1")],
        [(b"content-length", b"wrong"), (b"content-length", b"5")],
    ],
)
def test_any_declared_oversize_is_rejected_without_reading(headers):
    async def receive():
        pytest.fail("Declared oversize body was consumed")

    events = []

    async def send(message):
        events.append(message)

    async def app(*args):
        pytest.fail("Declared oversize body reached the application")

    guard = BoundedRequestBody(app, policy=RequestBodyPolicy(body_limit=4))
    asyncio.run(guard({"type": "http", "headers": headers}, receive, send))
    assert events[0]["status"] == 413


@pytest.mark.parametrize("rejection", ["json", "image"])
@pytest.mark.parametrize(
    "chunks",
    [
        [{"type": "http.disconnect"}],
        [
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.disconnect"},
        ],
    ],
)
def test_peer_disconnection_never_runs_application_or_sends_response(rejection, chunks):
    assert exchange(chunks, rejection=rejection) == ([], [], [])


@pytest.mark.parametrize("rejection", ["json", "image"])
def test_incomplete_body_releases_request_and_cancels_reader(rejection):
    events, cancelled = [], []

    async def receive():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def send(message):
        events.append(message)

    async def app(*args):
        pytest.fail("Incomplete body reached application")

    guard = BoundedRequestBody(
        app, policy=RequestBodyPolicy(body_limit=4, body_timeout_ms=10, rejection=rejection)
    )

    async def run():
        await asyncio.wait_for(guard({"type": "http"}, receive, send), timeout=1)

    asyncio.run(run())
    assert events[0]["status"] == 408 and cancelled == [True]
    assert json.loads(events[1]["body"]) == {
        "detail": "Incomplete request" if rejection == "json" else "Image upload timed out"
    }
    assert dict(events[0]["headers"]).get(b"cache-control") == (
        b"no-store" if rejection == "json" else None
    )


def test_image_oversize_retains_the_upload_message_without_adding_cache_headers():
    calls, received, events = exchange(
        [{"type": "http.request", "body": b"large", "more_body": False}], rejection="image"
    )
    assert not calls and not received and events[0]["status"] == 413
    assert json.loads(events[1]["body"]) == {"detail": "Choose an image under 5 MB"}
    assert b"cache-control" not in dict(events[0]["headers"])


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
def test_other_protocols_are_unchanged(scope_type):
    message = {"type": "test.message", "body": b"large data"}
    calls, received, events = exchange([message], scope_type=scope_type)
    assert len(calls) == 1 and received[0] == message and not events


def test_receiving_chunks_does_not_restart_the_request_deadline():
    events, received = [], []

    async def receive():
        if received:
            await asyncio.sleep(0.01)
        received.append(True)
        return {"type": "http.request", "body": b"x", "more_body": True}

    async def send(message):
        events.append(message)

    async def app(*args):
        pytest.fail("A continuously streaming body outlived its deadline")

    guard = BoundedRequestBody(app, policy=RequestBodyPolicy(body_limit=1000, body_timeout_ms=30))

    async def run():
        await asyncio.wait_for(guard({"type": "http"}, receive, send), timeout=1)

    asyncio.run(run())
    assert events[0]["status"] == 408
    assert 0 < len(received) < 1000


def test_read_failures_are_not_rewritten_as_size_or_deadline_errors():
    failure = RuntimeError("Reader disconnected unexpectedly")

    async def receive():
        raise failure

    async def app(*args):
        pytest.fail("Broken reader reached the application")

    async def send(message):
        pytest.fail("Broken reader was reported as a policy rejection")

    guard = BoundedRequestBody(app, policy=RequestBodyPolicy(body_limit=4))
    with pytest.raises(RuntimeError) as error:
        asyncio.run(guard({"type": "http"}, receive, send))
    assert error.value is failure
