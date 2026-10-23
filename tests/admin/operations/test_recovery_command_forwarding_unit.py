"""Operator recovery commands keep durable identities and surface domain conflicts."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import ai_connection, notifications, source_imports, sources
from devfeed_core import job_retries
from devfeed_core.models import Source

from tests.admin.operations.test_codex_protocol_guard_policy_unit import (
    fixed_monitor_clock as fixed_monitor_clock,
)
from tests.admin.operations.test_codex_protocol_guard_policy_unit import monitor, pending
from tests.admin.sources.test_import_policy_unit import candidate_detail
from tests.notifications.test_notification_configuration_recovery_contracts_unit import (
    selected_delivery,
)
from tests.support.admin_editorial import operator_api
from tests.support.source_readiness import NOW, source


@pytest.mark.parametrize("state", ["missing", "running", "expired", "failed"])
def test_notification_retry_keeps_the_delivery_identity_and_commits_only_an_admitted_retry(
    monkeypatch, state
):
    current = selected_delivery(age=timedelta(days=28) if state == "expired" else timedelta(days=2))
    current.status = "running" if state == "running" else "failed"
    if current.status == "failed":
        current.finished_at = NOW - timedelta(minutes=5)
        current.error = "Upstream unavailable"
        current.lease_token = current.lease_until = current.dispatched_at = None
    identifier, event, dedup = current.id, current.event_key, current.dedup_key
    monkeypatch.setattr(job_retries, "utcnow", lambda: NOW)
    with operator_api(notifications) as api:
        api.session.scalar.return_value = None if state == "missing" else current
        response = api.client.post(f"/v1/admin/notifications/deliveries/{identifier}/retry")
        assert response.status_code == (
            200 if state == "failed" else 404 if state == "missing" else 409
        )
        if state == "failed":
            output = response.json()
            assert (output["id"], output["kind"], output["status"], output["attempts"]) == (
                str(identifier),
                "notifications",
                "queued",
                0,
            )
            assert (current.event_key, current.dedup_key) == (event, dedup)
            assert current.available_at == NOW and current.finished_at is current.error is None
            api.session.commit.assert_called_once_with()
        else:
            api.session.commit.assert_not_called()


@pytest.mark.parametrize("state", ["current", "old-identity", "transport-failure"])
def test_codex_cancel_http_operation_reports_current_attempt_outcome_without_provider_details(
    state,
):
    client, ws = monitor([{"id": 1, "result": {}}])
    pending(client)
    if state == "transport-failure":
        ws.recv.side_effect = OSError("private websocket credential")
    with operator_api(ai_connection) as api:
        api.app.state.codex = client
        response = api.client.post(
            "/v1/admin/ai/connection/login/cancel",
            json={"login_id": "previous-login" if state == "old-identity" else "current-login"},
        )
        assert response.status_code == (204 if state == "current" else 409)
        assert "private" not in response.text
        if state == "current":
            assert response.headers["cache-control"] == "no-store"
            assert client.status.login.status == "cancelled"
            assert client.status.login.user_code is client.status.login.verification_url is None
        elif state == "old-identity":
            assert client.status.login.status == "pending"
            ws.send.assert_not_awaited()
        else:
            assert client.status.login.status == "failed" and client.ws is None


def test_import_detail_preserves_the_current_discovery_document_without_writing(monkeypatch):
    identifier = uuid.uuid4()
    current = candidate_detail(identifier)
    show = Mock(return_value=current)
    monkeypatch.setattr(source_imports.discovery, "show", show)
    with operator_api(source_imports) as api:
        response = api.client.get(f"/v1/admin/source-imports/{identifier}")
        assert response.status_code == 200, response.text
        assert response.json() == current.model_dump(mode="json")
        show.assert_called_once_with(identifier)
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("bulk", [False, True])
def test_import_delete_returns_the_domain_count_for_exact_requested_candidate_identities(
    monkeypatch, bulk
):
    identifiers = [uuid.uuid4(), uuid.uuid4()] if bulk else [uuid.uuid4()]
    delete = Mock(return_value={"deleted": len(identifiers)})
    monkeypatch.setattr(source_imports.discovery, "delete_candidates", delete)
    with operator_api(source_imports) as api:
        response = (
            api.client.post(
                "/v1/admin/source-imports/bulk-delete", json={"ids": list(map(str, identifiers))}
            )
            if bulk
            else api.client.delete(f"/v1/admin/source-imports/{identifiers[0]}")
        )
        assert response.status_code == 200 and response.json() == {"deleted": len(identifiers)}
        delete.assert_called_once_with(identifiers)
        # The discovery command owns its scope; this adapter must not introduce another commit.
        api.session.commit.assert_not_called()


def test_source_delete_requests_only_dependent_ingestion_identity_and_origin_rows(monkeypatch):
    current = source()
    require = Mock(return_value=current)
    monkeypatch.setattr(sources, "record", require)
    with operator_api(sources) as api:
        response = api.client.delete(f"/v1/admin/sources/{current.id}")
        assert response.status_code == 204 and response.content == b""
        require.assert_called_once_with(api.session, Source, current.id, lock=True)
        # Observe requested write targets only. WHERE/cascade/lock correctness
        # and retained articles' visibility remain PostgreSQL integration contracts.
        assert [call.args[0].table.name for call in api.session.execute.call_args_list] == [
            "article_origins",
            "ingestion_jobs",
            "source_candidates",
            "sources",
        ]
        api.session.commit.assert_called_once_with()
