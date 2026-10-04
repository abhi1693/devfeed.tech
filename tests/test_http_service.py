"""Process ownership survives partial startup and failed cleanup."""

import asyncio
import importlib
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_http import service as runtime
from fastapi import FastAPI
from fastapi.responses import JSONResponse


@pytest.mark.parametrize("api", ["devfeed_api", "devfeed_user_api", "devfeed_admin_api"])
@pytest.mark.parametrize("healthy", [True, False])
def test_readiness_releases_overridden_database_session_before_response(api, healthy, monkeypatch):
    module = importlib.import_module(f"{api}.main")
    dependencies = importlib.import_module(f"{api}.dependencies")
    session, redis = object(), object()
    events = []

    def get_session():
        events.append("opened")
        try:
            yield session
        finally:
            events.append("closed")

    def readiness(current_session, current_redis, *args, **kwargs):
        assert current_session is session
        assert current_redis is redis
        assert events == ["opened"]
        if healthy:
            return {"status": "ok"}
        return JSONResponse({"status": "unhealthy"}, status_code=503)

    monkeypatch.setattr(module, "get_redis", lambda: redis)
    monkeypatch.setattr(runtime, "readiness_response", readiness)
    app = module.create_app()
    app.dependency_overrides[dependencies.get_session] = get_session
    responses = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        assert events == ["opened", "closed"]
        responses.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/health/ready",
                "raw_path": b"/health/ready",
                "query_string": b"",
                "root_path": "",
                "headers": [(b"host", b"localhost")],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 80),
            },
            receive,
            send,
        )
    )
    assert responses[0]["type"] == "http.response.start"
    assert responses[0]["status"] == (200 if healthy else 503)


@pytest.mark.parametrize(
    "failure", [None, "startup", "request", "resources", "clients", "telemetry"]
)
def test_lifecycle_always_releases_resources_clients_and_telemetry(failure, monkeypatch):
    events, blocking_threads = [], []
    telemetry = object()
    monkeypatch.setattr(runtime, "start_runtime", lambda name: telemetry)

    def close_clients():
        blocking_threads.append(threading.get_ident())
        events.append("clients")
        if failure == "clients":
            raise RuntimeError("clients")

    def stop(token):
        assert token is telemetry
        blocking_threads.append(threading.get_ident())
        events.append("telemetry")
        if failure == "telemetry":
            raise RuntimeError("telemetry")

    monkeypatch.setattr(runtime, "stop_runtime", stop)

    @asynccontextmanager
    async def resources(app):
        events.append("start")
        try:
            if failure == "startup":
                raise RuntimeError("startup")
            yield
        finally:
            events.append("resources")
            if failure == "resources":
                raise RuntimeError("resources")

    logger = Mock()
    service = runtime.HTTPService("test-api", logger, close_clients, resources)

    async def scenario():
        loop_thread = threading.get_ident()
        async with service.lifespan(FastAPI()):
            events.append("request")
            if failure == "request":
                raise RuntimeError("request")
        assert all(thread != loop_thread for thread in blocking_threads)

    if failure:
        with pytest.raises(RuntimeError, match=failure):
            asyncio.run(scenario())
    else:
        asyncio.run(scenario())
    assert events[-3:] == ["resources", "clients", "telemetry"]
    assert len(blocking_threads) == 2
    logger.info.assert_any_call("test_api_started")
    logger.info.assert_any_call("test_api_stopped")


@pytest.mark.parametrize("failure", ["start", "snapshots", "panels", "codex"])
def test_admin_owned_resources_cannot_prevent_process_cleanup(failure, monkeypatch):
    module = importlib.import_module("devfeed_admin_api.main")
    app = module.create_app()
    events = []
    monkeypatch.setattr(runtime, "start_runtime", lambda name: None)
    monkeypatch.setattr(runtime, "stop_runtime", lambda token: events.append("telemetry"))
    monkeypatch.setattr(module, "close_clients", lambda: events.append("clients"))

    def start():
        events.append("start")
        if failure == "start":
            raise RuntimeError("start")

    async def close(name, *args):
        events.append(name)
        if failure == name:
            raise RuntimeError(name)

    app.state.codex = SimpleNamespace(start=start, close=lambda: close("codex"))
    monkeypatch.setattr(module.overview, "close_snapshot_tasks", lambda app: close("snapshots"))
    monkeypatch.setattr(module.overview_panels, "close_panel_tasks", lambda app: close("panels"))

    async def scenario():
        async with module.lifespan(app):
            events.append("request")

    with pytest.raises(RuntimeError, match=failure):
        asyncio.run(scenario())
    assert events[-3:] == ["codex", "clients", "telemetry"]
    if failure != "start":
        assert events == [
            "start",
            "request",
            "snapshots",
            "panels",
            "codex",
            "clients",
            "telemetry",
        ]


def test_failed_reporting_cleanup_still_closes_admin_shared_clients(monkeypatch):
    module = importlib.import_module("devfeed_admin_api.main")
    closed = []

    def fail():
        raise RuntimeError("reporting")

    monkeypatch.setattr(module, "close_reporting", fail)
    monkeypatch.setattr(module, "close_shared_clients", closed.append)
    with pytest.raises(RuntimeError, match="reporting"):
        module.close_clients()
    assert closed == [module.get_redis]
