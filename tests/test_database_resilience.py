"""Connection policy and health behavior without external dependencies."""

import asyncio
import importlib
import threading
from types import SimpleNamespace

import anyio
import httpx
import pytest
from devfeed_core.config import Settings
from devfeed_core.db import create_database_engine
from pydantic import ValidationError
from sqlalchemy.pool import NullPool, QueuePool


def settings(**values):
    return Settings(
        database_url="postgresql+psycopg://test@database.invalid/recovery_test",
        redis_url="redis://redis.invalid/15",
        **values,
    )


def test_pool_policy_applies_to_queue_and_null_pools():
    for enabled in (True, False):
        engine = create_database_engine(settings(database_pool_enabled=enabled))
        try:
            assert isinstance(engine.pool, QueuePool if enabled else NullPool)
            if enabled:
                assert engine.pool.timeout() == 2
                assert engine.pool._recycle == 300
            assert engine.pool._pre_ping
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    "name,value",
    [
        ("database_pool_timeout_seconds", 0),
        ("database_connect_timeout_seconds", 1),
        ("database_keepalives_idle_seconds", 0),
        ("database_keepalives_interval_seconds", 0),
        ("database_keepalives_count", 0),
        ("database_tcp_user_timeout_ms", 0),
    ],
)
def test_failure_detection_cannot_be_disabled_accidentally(name, value):
    with pytest.raises(ValidationError):
        settings(**{name: value})


@pytest.mark.parametrize("service", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api"])
def test_liveness_does_not_wait_for_sync_request_threads(service, monkeypatch):
    main = importlib.import_module(service + ".main")
    monkeypatch.setattr(main, "get_redis", lambda: SimpleNamespace(ping=lambda: True))
    app = main.create_app()

    async def run():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        started = threading.Event()
        release = threading.Event()

        def busy():
            started.set()
            release.wait(5)

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(anyio.to_thread.run_sync, busy)
                while not started.is_set():
                    await anyio.sleep(0.001)
                try:
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app), base_url="http://test"
                    ) as client:
                        with anyio.fail_after(1):
                            assert (await client.get("/health/live")).status_code == 200
                finally:
                    release.set()
        finally:
            limiter.total_tokens = previous

    asyncio.run(run())
