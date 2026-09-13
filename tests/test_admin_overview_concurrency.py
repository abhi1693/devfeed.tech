"""Dashboard refreshes must leave request threads and the one-slot pool available."""

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from devfeed_admin_api import main, overview
from devfeed_admin_api.dependencies import get_session
from devfeed_core import cache
from devfeed_core.config import get_settings
from devfeed_http.dependencies import session_dependency
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture
def single_connection(database, admin_client, monkeypatch):
    engine = create_engine(database.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=2)
    sessions = sessionmaker(engine)
    app = admin_client.app
    app.dependency_overrides[get_session] = session_dependency(lambda: sessions)
    app.dependency_overrides[overview.session_factory] = lambda: sessions
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    yield engine, sessions
    app.dependency_overrides.pop(get_session)
    app.dependency_overrides.pop(overview.session_factory)
    cache.close_cache()
    engine.dispose()


def test_dashboard_waiters_share_refresh_without_blocking_other_pages(
    single_connection, admin_client, monkeypatch
):
    engine, _ = single_connection
    response_cache = cache.get_cache()
    entered = threading.Event()
    release = threading.Event()
    original_publish = response_cache.publish
    original_metrics = overview.overview_metrics
    loads = []
    loop_threads = []

    def metrics(session, days):
        assert threading.get_ident() not in loop_threads
        loads.append(days)
        return original_metrics(session, days)

    def publish(*args):
        assert threading.get_ident() not in loop_threads
        assert engine.pool.checkedout() == 0
        entered.set()
        assert release.wait(8), "test did not release cache publication"
        return original_publish(*args)

    monkeypatch.setattr(overview, "overview_metrics", metrics)
    monkeypatch.setattr(response_cache, "publish", publish)

    async def burst():
        loop_threads.append(threading.get_ident())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_client.app), base_url="http://testserver"
        ) as client:
            leader = asyncio.create_task(client.get("/v1/admin/overview"))
            waiters = []
            try:
                async with asyncio.timeout(3):
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                # More waiters than the default request thread pool has slots.
                waiters = [asyncio.create_task(client.get("/v1/admin/overview")) for _ in range(50)]
                await asyncio.sleep(0.15)
                assert not any(task.done() for task in waiters)
                async with asyncio.timeout(2):
                    others = await asyncio.gather(
                        client.get("/v1/admin/settings"),
                        client.get("/health/ready"),
                        client.get("/health/live"),
                    )
                assert all(response.status_code == 200 for response in others)
            finally:
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(leader, *waiters), timeout=5)
            assert len(responses) == 51
            assert all(response.status_code == 200 for response in responses)
            assert all(response.json() == responses[0].json() for response in responses)
            assert all(response.headers["cache-control"] == "no-store" for response in responses)

    asyncio.run(burst())
    assert loads == [30]
    assert engine.pool.checkedout() == 0


def test_abandoned_refresh_has_bounded_wait_without_database_load(
    single_connection, admin_client, monkeypatch
):
    engine, _ = single_connection
    response_cache = cache.get_cache()
    lookup = response_cache.lookup("admin-overview-v1:30", "admin-overview")
    monkeypatch.setattr(overview, "REFRESH_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(overview, "overview_metrics", lambda *_: pytest.fail("duplicate DB load"))
    try:
        response = admin_client.get("/v1/admin/overview")
        assert response.status_code == 503
        assert response.headers["retry-after"] == "2"
        assert engine.pool.checkedout() == 0
    finally:
        response_cache.release(lookup)


def test_local_refresh_timeout_does_not_unlock_the_active_loader(
    single_connection, admin_client, monkeypatch
):
    engine, _ = single_connection
    monkeypatch.setattr(overview, "REFRESH_WAIT_SECONDS", 0.05)

    async def scenario():
        lock = asyncio.Lock()
        admin_client.app.state.overview_locks[30] = lock
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_client.app), base_url="http://testserver"
        ) as client:
            async with lock:
                response = await client.get("/v1/admin/overview")
                assert response.status_code == 503
                assert response.headers["retry-after"] == "2"
                assert lock.locked()
                assert engine.pool.checkedout() == 0
            assert (await client.get("/v1/admin/overview")).status_code == 200

    asyncio.run(scenario())


def test_warm_dashboard_cache_still_requires_admin_authentication(
    single_connection, admin_client, monkeypatch
):
    from devfeed_admin_api import auth
    from devfeed_admin_api.auth import require_admin
    from devfeed_admin_api.config import Settings

    # This test exercises missing authentication, not missing OIDC configuration.
    # Do not depend on another test having populated the settings cache.
    settings = Settings(
        _env_file=None,
        admin_base_url="https://admin.example",
        oidc_issuer_url="https://identity.example",
        oidc_client_id="test-client",
        oidc_organization_id="test-organization",
        oidc_token_endpoint_auth_method="none",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    assert admin_client.get("/v1/admin/overview").status_code == 200
    override = admin_client.app.dependency_overrides.pop(require_admin)
    try:
        assert admin_client.get("/v1/admin/overview").status_code == 401
    finally:
        admin_client.app.dependency_overrides[require_admin] = override


@pytest.mark.parametrize("cache_mode", ["disabled", "unavailable", "invalid", "miss"])
def test_dashboard_returns_connection_on_each_load_path(
    single_connection, admin_client, monkeypatch, cache_mode
):
    engine, _ = single_connection
    response_cache = cache.get_cache()
    if cache_mode == "disabled":
        monkeypatch.setattr(get_settings(), "cache_enabled", False)
    elif cache_mode == "unavailable":

        def unavailable(*_):
            raise cache.CacheUnavailable

        monkeypatch.setattr(response_cache, "lookup", unavailable)
    elif cache_mode == "invalid":
        lookup = response_cache.lookup("admin-overview-v1:30", "admin-overview")
        response_cache.publish(lookup, b'{"unexpected":true}', 60)
        response_cache.release(lookup)
    assert admin_client.get("/v1/admin/overview").status_code == 200
    assert engine.pool.checkedout() == 0


def test_failed_dashboard_load_releases_connection_and_refresh_lock(
    single_connection, admin_client, monkeypatch
):
    engine, _ = single_connection
    original = overview.overview_metrics

    def fail(session, days):
        session.execute(text("SELECT 1"))
        raise ValueError("diagnostic failure")

    monkeypatch.setattr(overview, "overview_metrics", fail)
    with pytest.raises(ValueError, match="diagnostic failure"):
        admin_client.get("/v1/admin/overview")
    assert engine.pool.checkedout() == 0
    monkeypatch.setattr(overview, "overview_metrics", original)
    assert admin_client.get("/v1/admin/overview").status_code == 200


def test_readiness_does_not_hold_database_connection_during_redis_ping(
    single_connection, admin_client, monkeypatch
):
    engine, sessions = single_connection

    def ping():
        assert engine.pool.checkedout() == 0
        with sessions() as session:
            assert session.scalar(text("SELECT 1")) == 1

    with monkeypatch.context() as scoped:
        scoped.setattr(main, "get_redis", lambda: SimpleNamespace(ping=ping))
        assert admin_client.get("/health/ready").status_code == 200


def test_settings_materialize_before_returning_database_connection(single_connection):
    from devfeed_admin_api.user_settings import get

    engine, sessions = single_connection
    admin = SimpleNamespace(issuer="test", subject="admin", organization_id="org")
    with sessions() as session:
        result = get(admin, session)
        assert engine.pool.checkedout() == 0
        assert result.model_dump()["defaults"]["overview_days"] == 30
