import uuid
from types import SimpleNamespace

import pytest
from devfeed_admin_api.dependencies import get_redis, get_session
from devfeed_core.job_logs import JOB_KINDS
from test_admin_auth import complete
from test_admin_auth import oidc_app as oidc_app
from test_job_logs import StreamStore


@pytest.fixture
def log_client(oidc_app):
    complete(oidc_app)
    store = StreamStore()
    session = SimpleNamespace(
        scalar=lambda *args: SimpleNamespace(status="succeeded", attempts=2), close=lambda: None
    )
    oidc_app.client.app.dependency_overrides[get_redis] = lambda: store
    oidc_app.client.app.dependency_overrides[get_session] = lambda: session
    return oidc_app.client, store, session


@pytest.mark.parametrize("kind", JOB_KINDS)
def test_log_endpoint_requires_admin_before_storage_access(oidc_app, kind):
    oidc_app.client.app.dependency_overrides[get_redis] = lambda: pytest.fail("Redis before auth")
    response = oidc_app.client.get(f"/v1/admin/jobs/{kind}/{uuid.uuid4()}/logs")
    assert response.status_code == 401


@pytest.mark.parametrize("kind", JOB_KINDS)
def test_all_kinds_return_bounded_logs_and_live_status(log_client, kind):
    client, store, session = log_client
    closed = []
    session.close = lambda: closed.append(True)
    response = client.get(f"/v1/admin/jobs/{kind}/{uuid.uuid4()}/logs")
    assert response.status_code == 200, response.text
    assert closed == [True]
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "items": [],
        "next_cursor": None,
        "has_more": False,
        "truncated": False,
        "unreadable_entries": 0,
        "retention_seconds": 604800,
        "max_entries": 1000,
        "job_status": "succeeded",
        "attempts": 2,
    }
    assert store.executions == 1


@pytest.mark.parametrize(
    "query", ["after=bad", "after=18446744073709551616-0", "limit=0", "limit=501"]
)
def test_log_query_validation(log_client, query):
    client, store, _ = log_client
    response = client.get(f"/v1/admin/jobs/ingestion/{uuid.uuid4()}/logs?{query}")
    assert response.status_code == 422 and store.executions == 0


def test_missing_job_does_not_read_orphaned_logs(log_client):
    client, store, session = log_client
    session.scalar = lambda *args: None
    response = client.get(f"/v1/admin/jobs/images/{uuid.uuid4()}/logs")
    assert response.status_code == 404 and store.executions == 0


def test_storage_outage_is_not_reported_as_empty_logs(log_client):
    client, store, _ = log_client
    store.unavailable = True
    response = client.get(f"/v1/admin/jobs/analysis/{uuid.uuid4()}/logs")
    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]
    assert "secret" not in response.text


def test_unknown_job_kind(log_client):
    client, store, _ = log_client
    assert client.get(f"/v1/admin/jobs/unknown/{uuid.uuid4()}/logs").status_code == 422
    assert store.executions == 0
