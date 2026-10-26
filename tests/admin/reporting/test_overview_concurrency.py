"""Dashboard refreshes must leave request threads and the one-slot pool available."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from devfeed_admin_api import main, overview
from devfeed_admin_api import overview_projection as projections
from devfeed_admin_api.dependencies import get_session
from devfeed_core import cache
from devfeed_core.config import get_settings
from devfeed_http.dependencies import session_dependency
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def allow_intentional_fifty_request_stress(monkeypatch):
    # This suite proves thread/session cleanup with >40 simultaneous requests.
    # Admission overload is tested independently in test_http_admission.py;
    # keep a finite limit above this deliberately oversized regression workload.
    monkeypatch.setenv("DEVFEED_API_MAX_CONCURRENT_REQUESTS", "64")
    from devfeed_core.config import get_settings

    get_settings.cache_clear()


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


def test_cold_dashboard_burst_records_one_demand_without_loading(
    single_connection, admin_client, monkeypatch
):
    from devfeed_core.models import ReportRefresh
    from sqlalchemy import func, select

    engine, sessions = single_connection
    monkeypatch.setattr(
        projections, "calculate_report_async", lambda *_: pytest.fail("GET computed report")
    )

    async def burst():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_client.app), base_url="http://testserver"
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *[client.get("/v1/admin/overview/charts/attention") for _ in range(50)]
                ),
                5,
            )
            assert all(response.status_code == 503 for response in responses)
            others = await asyncio.gather(
                client.get("/v1/admin/settings"), client.get("/health/live")
            )
            assert all(response.status_code == 200 for response in others)

    asyncio.run(burst())
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 1
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("pipeline_ready", [True, False])
def test_stale_dashboard_returns_connection_and_survives_cache_failure(
    single_connection, admin_client, monkeypatch, pipeline_ready
):
    from datetime import timedelta

    from devfeed_admin_api.pipeline_projection import drain_pipeline
    from devfeed_core.models import utcnow

    from tests.support.overview import save_section

    engine, sessions = single_connection
    if pipeline_ready:
        drain_pipeline(sessions)
    save_section(sessions, "operations", 1, generated_at=utcnow() - timedelta(days=2))
    monkeypatch.setattr(cache, "get_cache", lambda: pytest.fail("Redis contacted while serving"))
    monkeypatch.setattr(
        projections, "calculate_report_async", lambda *_: pytest.fail("stale GET rebuilt")
    )
    response = admin_client.get("/v1/admin/overview/charts/attention")
    assert response.status_code == 200
    assert response.headers["x-overview-stale"] == "true"
    assert response.json()["snapshot"]["status"] == (
        "refreshing" if pipeline_ready else "rebuilding"
    )
    assert engine.pool.checkedout() == 0


def test_saved_dashboard_still_requires_admin_authentication(
    single_connection, admin_client, monkeypatch
):
    from devfeed_admin_api import auth
    from devfeed_admin_api.auth import require_admin

    from tests.support.overview import save_section

    _, sessions = single_connection
    save_section(sessions, "operations", 1)
    monkeypatch.setattr(auth, "require_config", lambda: None)
    assert admin_client.get("/v1/admin/overview/charts/attention").status_code == 200
    override = admin_client.app.dependency_overrides.pop(require_admin)
    try:
        assert admin_client.get("/v1/admin/overview/charts/attention").status_code == 401
    finally:
        admin_client.app.dependency_overrides[require_admin] = override


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
        response = admin_client.get("/health/ready")
        assert response.status_code == 200, response.text


def test_settings_materialize_before_returning_database_connection(single_connection):
    from devfeed_admin_api.user_settings import get

    engine, sessions = single_connection
    admin = SimpleNamespace(issuer="test", subject="admin", organization_id="org")
    with sessions() as session:
        result = get(admin, session)
        assert engine.pool.checkedout() == 0
        assert result.model_dump()["defaults"]["overview_days"] == 30


def test_reporting_deadline_cancels_statement_and_closes_connection(database):
    from devfeed_admin_api.reporting import reporting_engine
    from sqlalchemy.exc import DBAPIError

    engine = reporting_engine()
    with pytest.raises(DBAPIError, match="statement timeout"), engine.connect() as connection:
        connection.info["report_deadline"] = time.monotonic() + 0.05
        connection.execute(text("SELECT pg_sleep(1)"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
