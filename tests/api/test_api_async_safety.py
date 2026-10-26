"""Real ASGI concurrency checks; slow dependencies must not stop unrelated requests."""

import asyncio
import importlib
import inspect
import threading
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
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


def test_analytical_metadata_session_lifetime_stays_in_one_worker():
    from devfeed_admin_api.dependencies import read_metadata

    async def scenario():
        loop_thread = threading.get_ident()
        events = []

        class Session:
            def __init__(self):
                self.owner = threading.get_ident()
                assert self.owner != loop_thread

            def __enter__(self):
                assert threading.get_ident() == self.owner
                events.append("open")
                return self

            def __exit__(self, *_args):
                assert threading.get_ident() == self.owner
                events.append("close")

        def operation(session, fail):
            assert threading.get_ident() == session.owner
            events.append("query")
            if fail:
                raise RuntimeError("metadata unavailable")
            return "page metadata"

        assert await read_metadata(Session, operation, False) == "page metadata"
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            await read_metadata(Session, operation, True)
        assert events == ["open", "query", "close"] * 2

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["article", "source"])
@pytest.mark.parametrize("column", [False, True])
def test_pipeline_metadata_reads_release_sessions_around_clickhouse(kind, column):
    from devfeed_admin_api import article_pipeline, source_pipeline

    async def scenario():
        loop_thread = threading.get_ident()
        identifier, candidate_id = uuid.uuid4(), uuid.uuid4()
        next_check = datetime(2026, 10, 27, tzinfo=UTC)
        items = [SimpleNamespace(id=identifier, kind="source", state="running", next_check_at=None)]
        if kind == "source":
            items.append(SimpleNamespace(id=candidate_id, kind="candidate"))
        result = SimpleNamespace(columns=[SimpleNamespace(items=items)])
        active, statements, sessions = [], [], []

        class Session:
            def __init__(self):
                self.thread = threading.get_ident()
                assert self.thread != loop_thread
                sessions.append(self)

            def __enter__(self):
                active.append(self)
                return self

            def __exit__(self, *_args):
                assert threading.get_ident() == self.thread
                assert active.pop() is self

            def record(self, statement):
                assert threading.get_ident() == self.thread
                statements.append(statement)

            def scalar(self, statement):
                self.record(statement)
                return "istanbul"

            def scalars(self, statement):
                self.record(statement)
                return [identifier] if len(statements) == 2 else [candidate_id]

            def execute(self, statement):
                self.record(statement)
                names = [field.name for field in statement.selected_columns]
                if names == ["id", "automation_next_check_at"]:
                    return SimpleNamespace(all=lambda: [(identifier, next_check)])
                if names == ["id", "website_url", "feed_url"]:
                    return [
                        SimpleNamespace(id=identifier, website_url="private/site", feed_url=None)
                    ]
                return SimpleNamespace(all=lambda: [(candidate_id, "private/candidate")])

        class Analytics:
            async def article_board(self, **parameters):
                assert not active, "PostgreSQL session survived into the ClickHouse await"
                assert parameters["q"] == "İstanbul"
                assert parameters["normalized_q"] == "istanbul"
                assert parameters.get("stage") == ("analysis" if column else None)
                await asyncio.sleep(0)
                return result

            async def source_board(self, query, **parameters):
                assert not active, "PostgreSQL session survived into the ClickHouse await"
                assert query.q == "private/path"
                assert parameters["matching_ids"] == {
                    "source": [identifier],
                    "candidate": [candidate_id],
                }
                assert parameters.get("stage") == ("review" if column else None)
                await asyncio.sleep(0)
                return result

        if kind == "article":
            arguments = {"factory": Session, "analytics": Analytics(), "q": "İstanbul"}
            response = (
                await article_pipeline.column(stage="analysis", **arguments)
                if column
                else await article_pipeline.board(**arguments)
            )
            assert items[0].next_check_at == next_check
            assert len(statements) == 2
            assert statements[0].get_final_froms() == []
        else:
            query = SimpleNamespace(q="private/path")
            arguments = {"factory": Session, "analytics": Analytics(), "query": query}
            response = (
                await source_pipeline.column(stage="review", cursor=0, **arguments)
                if column
                else await source_pipeline.board(**arguments)
            )
            assert items[0].website_url == "private/site"
            assert items[1].website_url == "private/candidate"
            assert len(statements) == 5
            assert "2000" in statements[0].compile().params.values()
            assert all(
                [field.name for field in statement.selected_columns] == ["id"]
                for statement in statements[1:3]
            )
        assert response is (result.columns[0] if column else result)
        assert len(sessions) == 2
        assert not active

    asyncio.run(scenario())


@pytest.mark.parametrize("package", SERVICES)
def test_liveness_responds_while_the_entire_request_thread_pool_is_busy(package, monkeypatch):
    module = importlib.import_module(f"{package}.main")
    dependencies = importlib.import_module(f"{package}.dependencies")
    app = module.create_app()
    app.dependency_overrides[dependencies.get_session] = lambda: Session()
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
    from devfeed_http.inbox import INBOX_OPERATIONS, InboxProxy, InboxSession

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
        session = InboxSession(
            api_url="https://inbox.test",
            environment="test",
            secret="test-secret",
            subscriber_id="test-subscriber",
            expires_at=9999999999,
            client_factory=factory,
        )
        operation = next(operation for operation in INBOX_OPERATIONS if operation.name == "Items")
        response = await InboxProxy().forward(operation, request, session)
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
            await module.resolve_inbox_session(None)
        assert error.value.status_code == 503

    asyncio.run(scenario())


def test_public_cache_redis_calls_are_off_the_event_loop(monkeypatch):
    from devfeed_api import cache
    from fastapi import APIRouter, FastAPI

    async def scenario():
        loop_thread = threading.get_ident()

        def lookup(*args, **kwargs):
            assert threading.get_ident() != loop_thread
            return SimpleNamespace(body=b"[]")

        monkeypatch.setattr(cache, "get_cache", lambda: SimpleNamespace(lookup=lookup))
        monkeypatch.setattr(
            cache, "get_settings", lambda: SimpleNamespace(cache_enabled=True, cache_ttl_seconds=60)
        )
        app = FastAPI()
        router = APIRouter(route_class=cache.CachedReadRoute)

        @router.get(
            "/cached",
            openapi_extra=cache.CachePolicy(
                namespace="async-safety", revision=1, ttl="content"
            ).openapi_extra(),
        )
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
