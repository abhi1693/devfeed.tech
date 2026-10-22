"""Ingestion command composition over authoritative source/job read boundaries."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import config, jobs, polling, services
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import IngestionJob
from devfeed_core.schemas import SourceDecision
from test_source_readiness_unit import NOW, configuration, source


@pytest.mark.parametrize("approval", ["pending", "rejected"])
def test_request_requires_editorial_approval_before_any_job_read_or_write(approval):
    current, session = source(approval=approval), Mock()
    with pytest.raises(ValueError, match="Only approved"):
        jobs.request_ingestion(session, current, automatic=True)
    assert session.mock_calls == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_ingestion_is_coalesced_without_changing_its_due_time_or_lease(status):
    current = source(approval="approved")
    row = IngestionJob(
        id=uuid.uuid4(),
        source_id=current.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(minutes=10),
    )
    if status == "running":
        start_job(row, NOW - timedelta(minutes=1), 300)
        current.last_attempt_at = NOW - timedelta(minutes=1)
    before = copy.deepcopy(
        (row.status, row.available_at, row.attempts, row.lease_token, row.lease_until)
    )
    session = Mock()
    session.scalar.return_value = row
    assert jobs.request_ingestion(session, current, automatic=True) is row
    assert (row.status, row.available_at, row.attempts, row.lease_token, row.lease_until) == before
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("adaptive_failure", [False, True])
def test_new_ingestion_preserves_adaptive_failure_cooldown_without_delaying_a_healthy_request(
    monkeypatch, adaptive_failure
):
    settings = configuration(adaptive_source_polling_enabled=True)
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(polling, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    current = source(
        approval="approved",
        poll_interval_seconds=1800,
        polling_mode="adaptive" if adaptive_failure else "fixed",
        polling_state={"interval": 3600},
        consecutive_failures=2 if adaptive_failure else 0,
        next_fetch_at=NOW + timedelta(minutes=20),
    )
    session = Mock()
    session.scalar.return_value = None
    row = jobs.request_ingestion(session, current, automatic=True)
    assert isinstance(row, IngestionJob) and row.source_id == current.id and row.automatic is True
    assert row.available_at == (NOW + timedelta(minutes=20) if adaptive_failure else NOW)
    assert current.next_fetch_at >= row.available_at
    session.add.assert_called_once_with(row)
    session.flush.assert_called_once_with()


def test_late_failure_after_a_real_rejection_does_not_increment_upstream_failure_counters(
    monkeypatch,
):
    current = source(
        approval="approved",
        poll_interval_seconds=1800,
        consecutive_failures=2,
        next_fetch_at=NOW + timedelta(minutes=20),
        last_error="Prior upstream failure",
    )
    row = IngestionJob(
        id=uuid.uuid4(),
        source_id=current.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
    )
    start_job(row, NOW - timedelta(seconds=30), 300)
    session = Mock()
    session.scalar.return_value = current
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(services, "utcnow", lambda: NOW)
    services.review_source(
        session,
        current.id,
        SourceDecision(
            decision="rejected",
            actor="editor",
            note="The feed no longer serves the software audience.",
        ),
    )
    before = (
        current.consecutive_failures,
        current.next_fetch_at,
        current.last_error,
        current.last_success_at,
    )
    jobs.fail_job(session, row, "Upstream timeout", retryable=True)
    assert row.status == "failed" and row.error == "Source is not approved for ingestion"
    assert row.lease_token is row.lease_until is None and row.finished_at == NOW
    assert row.attempts == 1
    assert (
        current.consecutive_failures,
        current.next_fetch_at,
        current.last_error,
        current.last_success_at,
    ) == before


def test_delivery_for_a_deleted_source_and_cascaded_job_has_no_claim_or_attempt():
    session = Mock()
    session.scalar.return_value = None
    assert jobs.claim_job(session, uuid.uuid4()) is None
    assert session.scalar.call_count == 1
    session.add.assert_not_called()
    session.flush.assert_not_called()
