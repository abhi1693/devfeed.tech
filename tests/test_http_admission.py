"""Concurrency is bounded without holding DB connections or queueing HTTP work."""

import asyncio
import importlib

import httpx
import pytest
from devfeed_core.config import get_settings
from devfeed_http.admission import STREAM_PATHS, AdmissionMiddleware
from fastapi import FastAPI


@pytest.mark.parametrize("service", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api"])
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

        stream_path = sorted(STREAM_PATHS)[0]

        # These test routes run without identity/DB; production routes retain auth.
        test_stream = "/v1/user/notifications/chimely/v1/inbox/stream"
        if service == "devfeed_user_api":
            test_stream = stream_path

        @app.get(test_stream)
        async def stream():
            return {"ok": True}

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
                assert (await client.get(test_stream)).status_code == 200
            finally:
                release.set()
                assert (await active).status_code == 200
            assert (await client.get("/admission-test")).status_code == 200

    asyncio.run(run())


def test_admission_releases_capacity_after_cancellation_and_exception():
    async def run():
        entered = asyncio.Event()
        never = asyncio.Event()

        async def handler(scope, receive, send):
            if scope["path"] == "/cancel":
                entered.set()
                await never.wait()
            raise RuntimeError("test failure")

        middleware = AdmissionMiddleware(handler, requests=1, streams=1)

        async def unused(*args):
            return {}

        active = asyncio.create_task(
            middleware({"type": "http", "path": "/cancel"}, unused, unused)
        )
        await entered.wait()
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        for _ in range(2):
            with pytest.raises(RuntimeError, match="test failure"):
                await middleware({"type": "http", "path": "/fail"}, unused, unused)
        assert middleware.active == {"request": 0, "stream": 0}

    asyncio.run(run())


def test_notification_streams_have_a_separate_bounded_budget():
    async def run():
        app = FastAPI()
        app.add_middleware(AdmissionMiddleware, requests=1, streams=1)
        entered, release = asyncio.Event(), asyncio.Event()
        path = sorted(STREAM_PATHS)[0]

        @app.get(path)
        async def stream():
            entered.set()
            await release.wait()
            return {"ok": True}

        @app.get("/interactive")
        async def interactive():
            return {"ok": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get(path))
            await entered.wait()
            try:
                assert (await client.get(path)).status_code == 503
                assert (await client.get("/interactive")).status_code == 200
            finally:
                release.set()
                await active
            assert (await client.get(path)).status_code == 200

    asyncio.run(run())
