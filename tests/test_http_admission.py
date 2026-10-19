"""Concurrency is bounded without holding DB connections or queueing HTTP work."""

import asyncio
import importlib

import httpx
import pytest
from devfeed_core.config import get_settings
from devfeed_http.admission import ADMISSION_SCOPE_KEY, AdmissionController, AdmissionMiddleware
from devfeed_http.route_policies import RouteRuntimePolicy, install_runtime_policies
from fastapi import Depends, FastAPI
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, StreamingResponse


def bounded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AdmissionMiddleware, requests=1, streams=1)
    return app


@pytest.mark.parametrize(
    "service", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api", "devfeed_partner_api"]
)
def test_service_bounds_requests_but_keeps_liveness_and_streams_independent(service, monkeypatch):
    monkeypatch.setenv("DEVFEED_API_MAX_CONCURRENT_REQUESTS", "1")
    monkeypatch.setenv("DEVFEED_API_MAX_CONCURRENT_STREAMS", "1")
    get_settings.cache_clear()
    app = importlib.import_module(service + ".main").create_app()

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        @app.get("/admission-test")
        async def slow():
            entered.set()
            await release.wait()
            return {"ok": True}

        @app.get(
            "/renamed-events", openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra()
        )
        async def stream():
            return {"ok": True}

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/admission-test"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                rejected = await asyncio.wait_for(client.get("/admission-test"), 1)
                assert rejected.status_code == 503
                assert rejected.headers["retry-after"] == "1"
                assert rejected.headers["cache-control"] == "no-store"
                assert (await client.get("/health/live")).status_code == 200
                assert (await client.get("/renamed-events")).status_code == 200
            finally:
                release.set()
                assert (await active).status_code == 200
            assert (await client.get("/admission-test")).status_code == 200

    asyncio.run(run())


@pytest.mark.parametrize("lane", ["request", "stream"])
def test_admission_releases_capacity_after_cancellation_and_exception(lane):
    async def run():
        entered = asyncio.Event()
        never = asyncio.Event()

        async def blocked(scope, receive, send):
            entered.set()
            await never.wait()

        async def failing(scope, receive, send):
            raise RuntimeError("test failure")

        controller = AdmissionController(requests=1, streams=1)

        async def unused(*args):
            return {}

        scope = {"type": "http", "method": "GET"}
        active = asyncio.create_task(controller.run(lane, scope, unused, unused, blocked))
        await asyncio.wait_for(entered.wait(), 2)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        for _ in range(2):
            with pytest.raises(RuntimeError, match="test failure"):
                await controller.run(lane, scope, unused, unused, failing)
        assert controller.active == {"request": 0, "stream": 0}

    asyncio.run(run())


def test_each_lane_remains_available_when_the_other_lane_is_full():
    async def run():
        app = bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()
        request_entered, request_release = asyncio.Event(), asyncio.Event()

        @app.get("/events", openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra())
        async def stream():
            entered.set()
            await release.wait()
            return {"ok": True}

        @app.get("/interactive")
        async def interactive():
            return {"ok": True}

        @app.get("/held-request")
        async def held_request():
            request_entered.set()
            await request_release.wait()
            return {"ok": True}

        @app.get("/live", openapi_extra=RouteRuntimePolicy(admission="exempt").openapi_extra())
        async def live():
            return {"ok": True}

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/events"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                assert (await client.get("/events")).status_code == 503
                assert (await client.get("/interactive")).status_code == 200
                held = asyncio.create_task(client.get("/held-request"))
                await asyncio.wait_for(request_entered.wait(), 2)
                try:
                    assert (await client.get("/events")).status_code == 503
                    assert (await client.get("/interactive")).status_code == 503
                    assert (await client.get("/live")).status_code == 200
                    release.set()
                    assert (await active).status_code == 200
                    assert (await client.get("/events")).status_code == 200
                finally:
                    request_release.set()
                    assert (await held).status_code == 200
            finally:
                release.set()
                await active
            assert (await client.get("/events")).status_code == 200

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["body", "background"])
@pytest.mark.parametrize("lane", ["request", "stream"])
def test_admission_slot_covers_response_body_and_background_cleanup(phase, lane):
    async def run():
        app = bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()

        async def cleanup():
            entered.set()
            await release.wait()

        @app.get("/held", openapi_extra=RouteRuntimePolicy(admission=lane).openapi_extra())
        async def held():
            if phase == "background":
                return JSONResponse({"ok": True}, background=BackgroundTask(cleanup))

            async def chunks():
                yield b"first"
                await cleanup()
                yield b"last"

            return StreamingResponse(chunks())

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/held"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                assert (await client.get("/held")).status_code == 503
            finally:
                release.set()
                assert (await active).status_code == 200
            assert (await client.get("/held")).status_code == 200

    asyncio.run(run())


def test_overload_is_rejected_before_dependencies_and_body_reading():
    async def run():
        app = bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()
        body_read = asyncio.Event()
        dependencies = []

        async def dependency():
            dependencies.append(True)

        @app.post("/save", dependencies=[Depends(dependency)])
        async def save(payload: dict):
            entered.set()
            await release.wait()
            return payload

        class ProbeBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                body_read.set()
                yield b'{"second":true}'

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.post("/save", json={"first": True}))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                rejected = await client.post(
                    "/save", content=ProbeBody(), headers={"Content-Type": "application/json"}
                )
                assert rejected.status_code == 503
                assert dependencies == [True]
                assert not body_read.is_set()
            finally:
                release.set()
                assert (await active).status_code == 200

    asyncio.run(run())


@pytest.mark.parametrize(
    "path,method", [("/missing", "GET"), ("/live", "POST"), ("/events", "PUT")]
)
def test_unmatched_paths_and_wrong_methods_use_request_capacity(path, method):
    async def run():
        app = bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()

        @app.get("/held")
        async def held():
            entered.set()
            await release.wait()
            return {"ok": True}

        @app.get("/live", openapi_extra=RouteRuntimePolicy(admission="exempt").openapi_extra())
        async def live():
            return {"ok": True}

        @app.get("/events", openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra())
        async def stream():
            return {"ok": True}

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/held"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                assert (await client.request(method, path)).status_code == 503
            finally:
                release.set()
                assert (await active).status_code == 200
            assert (await client.request(method, path)).status_code == (
                404 if path == "/missing" else 405
            )

    asyncio.run(run())


def test_preflight_and_slash_redirect_fallbacks_are_bounded():
    async def run():
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://reader.example"],
            allow_methods=["GET"],
        )
        app.add_middleware(AdmissionMiddleware, requests=1, streams=1)
        entered, release = asyncio.Event(), asyncio.Event()

        @app.get("/held/")
        async def held():
            entered.set()
            await release.wait()
            return {"ok": True}

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/held/"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                preflight = await client.options(
                    "/held/",
                    headers={
                        "Origin": "https://reader.example",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert preflight.status_code == 503
                assert preflight.headers["access-control-allow-origin"] == "https://reader.example"
                assert {name.strip() for name in preflight.headers["vary"].split(",")} == {
                    "Origin",
                    "Access-Control-Request-Method",
                    "Access-Control-Request-Headers",
                    "Access-Control-Request-Private-Network",
                }
                assert preflight.headers["cache-control"] == "no-store"
                assert (await client.get("/held")).status_code == 503
            finally:
                release.set()
                assert (await active).status_code == 200
            assert (await client.get("/held")).status_code == 307

    asyncio.run(run())


def test_send_failure_releases_middleware_owned_capacity():
    async def run():
        async def response(scope, receive, send):
            await JSONResponse({"ok": True})(scope, receive, send)

        async def operation(scope, receive, send):
            await scope[ADMISSION_SCOPE_KEY].run("stream", scope, receive, send, response)

        async def fail_send(message):
            raise RuntimeError("transport disconnected")

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        middleware = AdmissionMiddleware(operation, requests=1, streams=1)
        with pytest.raises(RuntimeError, match="transport disconnected"):
            await middleware({"type": "http", "method": "GET"}, receive, fail_send)
        assert middleware.active == {"request": 0, "stream": 0}

    asyncio.run(run())


@pytest.mark.parametrize("path,method,status", [("/missing", "GET", 404), ("/exists", "POST", 405)])
def test_native_error_translation_keeps_the_admitted_slot(path, method, status):
    async def run():
        app = bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()

        @app.exception_handler(HTTPException)
        async def translate(request, exc):
            entered.set()
            await release.wait()
            return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)

        @app.get("/exists")
        async def exists():
            return {"ok": True}

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.request(method, path))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                assert (await client.get("/exists")).status_code == 503
            finally:
                release.set()
                assert (await active).status_code == status
            assert (await client.get("/exists")).status_code == 200

    asyncio.run(run())


def test_nested_providers_keep_a_mounted_stream_out_of_request_capacity():
    async def run():
        parent, child = bounded_app(), bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()

        @parent.get("/held")
        async def held():
            entered.set()
            await release.wait()
            return {"ok": True}

        @child.get("/events", openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra())
        async def stream():
            return {"ok": True}

        parent.mount("/mounted", child)
        install_runtime_policies(parent)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(parent), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/held"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                assert (await client.get("/mounted/events")).status_code == 200
            finally:
                release.set()
                assert (await active).status_code == 200

    asyncio.run(run())


def test_opaque_mounted_app_is_rejected_before_handler_work():
    async def run():
        parent = bounded_app()
        entered, release = asyncio.Event(), asyncio.Event()
        child_calls = []

        @parent.get("/held")
        async def held():
            entered.set()
            await release.wait()
            return {"ok": True}

        async def opaque(scope, receive, send):
            child_calls.append(True)
            await JSONResponse({"ok": True})(scope, receive, send)

        parent.mount("/opaque", opaque)
        install_runtime_policies(parent)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(parent), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/held"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                rejected = await client.get("/opaque/resource")
                assert rejected.status_code == 503
                assert rejected.headers["retry-after"] == "1"
                assert child_calls == []
            finally:
                release.set()
                assert (await active).status_code == 200
            assert (await client.get("/opaque/resource")).status_code == 200
            assert child_calls == [True]

    asyncio.run(run())


def test_middleware_wrapped_mounted_app_keeps_its_leaf_stream_lane():
    async def run():
        parent, child = bounded_app(), FastAPI()
        entered, release = asyncio.Event(), asyncio.Event()

        @parent.get("/held")
        async def held():
            entered.set()
            await release.wait()
            return {"ok": True}

        @child.get("/events", openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra())
        async def stream():
            return {"ok": True}

        parent.mount("/wrapped", CORSMiddleware(child, allow_origins=["https://reader.example"]))
        install_runtime_policies(parent)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(parent), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/held"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                response = await client.get(
                    "/wrapped/events", headers={"Origin": "https://reader.example"}
                )
                assert response.status_code == 200
                assert response.headers["access-control-allow-origin"] == "https://reader.example"
            finally:
                release.set()
                assert (await active).status_code == 200

    asyncio.run(run())


def test_admission_records_template_slot_ownership_until_cancelled(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from devfeed_http import admission

    instruments = {
        name: Mock()
        for name in (
            "admitted",
            "limit",
            "rejections",
            "admission_route_active",
            "admission_duration",
        )
    }
    monkeypatch.setattr(admission, "current", lambda: SimpleNamespace(instruments=instruments))
    controller = AdmissionController(requests=1, streams=1)
    lease = controller.acquire("request", route="/sources/{id}")
    assert controller.acquire("request", route="/feed") is None
    assert lease is not None
    lease.close()
    lease.close()
    attributes = {"kind": "request", "http.route": "/sources/{id}"}
    assert instruments["admission_route_active"].add.call_args_list == [
        ((1, attributes),),
        ((-1, attributes),),
    ]
    instruments["admission_duration"].record.assert_called_once()
    duration, labels = instruments["admission_duration"].record.call_args.args
    assert duration >= 0 and labels == attributes
    assert controller.active["request"] == 0
