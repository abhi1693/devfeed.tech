"""Dashboard refreshes must leave request threads and the one-slot pool available."""

from types import SimpleNamespace

import pytest
from devfeed_admin_api import main
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
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    yield engine, sessions
    app.dependency_overrides.pop(get_session)
    cache.close_cache()
    engine.dispose()


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
