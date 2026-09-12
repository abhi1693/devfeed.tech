"""Real ASGI concurrency checks; slow dependencies must not stop unrelated requests."""

import asyncio
import importlib
import inspect
import threading
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request

SERVICES = ("devfeed_api", "devfeed_admin_api", "devfeed_user_api")


def api_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router.routes)


@pytest.mark.parametrize("package", SERVICES)
def test_async_endpoints_do_not_receive_synchronous_database_sessions(package):
    module = importlib.import_module(f"{package}.main")
    dependencies = importlib.import_module(f"{package}.dependencies")
    for route in api_routes(module.create_app().routes):
        if inspect.iscoroutinefunction(route.endpoint):
            assert all(
                dep.call is not dependencies.get_session for dep in route.dependant.dependencies
            ), route.path


@pytest.mark.parametrize("package", SERVICES)
def test_liveness_responds_while_the_entire_request_thread_pool_is_busy(package, monkeypatch):
    module = importlib.import_module(f"{package}.main")
    dependencies = importlib.import_module(f"{package}.dependencies")
    app = module.create_app()
    app.dependency_overrides[dependencies.get_session] = lambda: object()
    release = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        started = asyncio.Event()
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1

        def revision(_session):
            assert threading.get_ident() != loop_thread
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3), "test did not release the blocked database call"
            return module.SCHEMA_REVISION

        monkeypatch.setattr(module, "database_revision", revision)
        monkeypatch.setattr(module, "get_redis", lambda: SimpleNamespace(ping=lambda: None))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                ready = asyncio.create_task(client.get("/health/ready"))
                try:
                    await asyncio.wait_for(started.wait(), timeout=1)
                    assert limiter.borrowed_tokens == 1
                    live = await asyncio.wait_for(client.get("/health/live"), timeout=0.5)
                    assert live.status_code == 200
                finally:
                    release.set()
                    response = await ready
                assert response.status_code == 200
        finally:
            release.set()
            limiter.total_tokens = previous

    asyncio.run(scenario())


@pytest.mark.parametrize("package", SERVICES)
def test_lifespan_closes_blocking_clients_outside_the_event_loop(package, monkeypatch):
    module = importlib.import_module(f"{package}.main")
    app = module.create_app()
    threads = []
    monkeypatch.setattr(module, "close_clients", lambda: threads.append(threading.get_ident()))

    async def scenario():
        loop_thread = threading.get_ident()
        async with module.lifespan(app):
            pass
        assert len(threads) == 1 and threads[0] != loop_thread

    asyncio.run(scenario())


def test_inbox_tls_initialization_does_not_run_on_the_event_loop():
    from devfeed_http.inbox import proxy_inbox

    async def scenario():
        loop_thread = threading.get_ident()
        clients = []

        def factory():
            assert threading.get_ident() != loop_thread
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))
            )
            clients.append(client)
            return client

        async def receive():
            return {"type": "http.request", "body": b""}

        request = Request(
            {"type": "http", "method": "GET", "headers": [], "query_string": b""}, receive
        )
        response = await proxy_inbox(
            "items",
            request,
            api_url="https://inbox.test",
            headers={},
            expires_at=9999999999,
            client_factory=factory,
        )
        assert response.status_code == 200
        assert clients[0].is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("package", ["devfeed_admin_api", "devfeed_user_api"])
def test_inbox_settings_file_loading_is_off_the_event_loop(package, monkeypatch):
    from fastapi import HTTPException

    module = importlib.import_module(f"{package}.notifications")

    async def scenario():
        loop_thread = threading.get_ident()

        def settings():
            assert threading.get_ident() != loop_thread
            return SimpleNamespace(notifications_enabled=False)

        monkeypatch.setattr(module, "get_settings", settings)
        with pytest.raises(HTTPException) as error:
            await module.inbox_proxy("items", None, None)
        assert error.value.status_code == 503

    asyncio.run(scenario())


def test_public_cache_redis_calls_are_off_the_event_loop(monkeypatch):
    from devfeed_api import cache
    from fastapi import APIRouter, FastAPI

    async def scenario():
        loop_thread = threading.get_ident()

        def lookup(*args):
            assert threading.get_ident() != loop_thread
            return SimpleNamespace(body=b"[]")

        monkeypatch.setattr(cache, "get_cache", lambda: SimpleNamespace(lookup=lookup))
        monkeypatch.setattr(
            cache, "get_settings", lambda: SimpleNamespace(cache_enabled=True, cache_ttl_seconds=60)
        )
        app = FastAPI()
        router = APIRouter(route_class=cache.CachedReadRoute)

        @router.get("/cached")
        def endpoint():
            raise AssertionError("a cache hit must not load the endpoint")

        app.include_router(router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get("/cached")
        assert response.status_code == 200 and response.headers["x-cache"] == "HIT"

    asyncio.run(scenario())


def test_codex_tls_trust_store_is_loaded_off_loop_and_reused(monkeypatch):
    import ssl
    from unittest.mock import AsyncMock

    from devfeed_admin_api import codex_connection
    from devfeed_core.config import Settings

    async def scenario():
        loop_thread = threading.get_ident()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        created = []

        def build_context():
            assert threading.get_ident() != loop_thread
            created.append(context)
            return context

        async def connect(_endpoint, **kwargs):
            assert kwargs["ssl"] is context
            return SimpleNamespace(send=AsyncMock(), close=AsyncMock())

        monkeypatch.setattr(codex_connection.ssl, "create_default_context", build_context)
        client = codex_connection.CodexConnection(
            Settings(
                _env_file=None,
                database_url="postgresql://db.invalid/audit_test",
                redis_url="redis://redis.invalid/15",
                ai_enabled=True,
                codex_model="test",
                codex_app_server_url="wss://codex.test",
                codex_auth_token="test-token",
            ),
            connector=connect,
        )
        monkeypatch.setattr(client, "_rpc", AsyncMock(return_value={}))
        await client._connect()
        await client._disconnect()
        await client._connect()
        await client.close()
        assert created == [context]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

    asyncio.run(scenario())
