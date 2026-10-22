"""Source submission and ingestion commands enforce operator authority before effects."""

import copy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import services, source_lifecycle
from devfeed_core.models import IngestionJob
from devfeed_core.schemas import SourceCreate, SourceDecision
from devfeed_core.services import OperationConflict, RecordNotFound
from test_source_readiness_unit import NOW, configuration, job, source


@pytest.mark.parametrize("name", [None, "Submitted publisher"])
def test_website_only_submission_has_a_stable_display_name_without_network_preflight(
    monkeypatch, name
):
    preflight = Mock()
    monkeypatch.setattr(services, "validate_feed", preflight)
    body = SourceCreate(
        name=name,
        source_type="publisher",
        website_url="https://publisher.example/about",
        feed_url=None,
    )
    original = body.model_dump()
    validated = services.validate_source(body)
    assert validated.name == (name or "publisher.example")
    assert validated.website_url == body.website_url and validated.feed_url is None
    assert body.model_dump() == original
    preflight.assert_not_called()


@pytest.mark.parametrize("guard", ["missing", "pending", "paused", "approved"])
def test_explicit_ingestion_requires_an_enabled_approved_source_before_forwarding(
    guard, monkeypatch
):
    current = source(
        approval="pending" if guard == "pending" else "approved", enabled=guard == "approved"
    )
    session = Mock()
    session.scalar.return_value = None if guard == "missing" else current
    request = Mock(return_value=job(IngestionJob, source_id=current.id))
    monkeypatch.setattr(services, "request_ingestion", request)
    if guard == "approved":
        assert services.fetch_source(session, current.id) is request.return_value
        request.assert_called_once_with(session, current)
    else:
        with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
            services.fetch_source(session, current.id)
        request.assert_not_called()


@pytest.mark.parametrize("guard", ["reason", "missing", "undiscovered", "processing"])
def test_source_decision_requires_a_rejection_reason_or_current_approval_prerequisites(
    monkeypatch, guard
):
    current = source(feed_url=None if guard == "undiscovered" else "https://publisher.example/feed")
    session = Mock()
    session.scalar.return_value = None if guard == "missing" else current
    blockers = Mock(return_value=["profile_running"] if guard == "processing" else [])
    monkeypatch.setattr(source_lifecycle, "source_activation_blockers", blockers)
    decision = SourceDecision(
        decision="rejected" if guard == "reason" else "approved", actor="Editor"
    )
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        services.review_source(session, current.id, decision)
    assert current.approval_status == "pending" and not current.enabled
    session.add.assert_not_called()
    session.flush.assert_not_called()
    if guard == "reason":
        session.scalar.assert_not_called()


@pytest.mark.parametrize("status", ["queued", "running", "succeeded"])
def test_ingestion_retry_does_not_steal_an_active_lease_or_repeat_a_successful_run(
    status, monkeypatch
):
    current = source(approval="approved")
    previous = job(IngestionJob, source_id=current.id, status=status)
    before = copy.deepcopy(
        (previous.status, previous.attempts, previous.lease_token, previous.lease_until)
    )
    session = Mock()
    session.get.return_value = previous
    fetch = Mock()
    monkeypatch.setattr(services, "fetch_source", fetch)
    with pytest.raises(OperationConflict, match="Only failed"):
        services.retry_job(session, previous.id)
    fetch.assert_not_called()
    assert before == (
        previous.status,
        previous.attempts,
        previous.lease_token,
        previous.lease_until,
    )


def test_failed_ingestion_retry_retains_the_history_and_rechecks_current_source_authority(
    monkeypatch,
):
    current = source(approval="approved")
    previous = job(IngestionJob, source_id=current.id, status="failed")
    replacement = job(IngestionJob, source_id=current.id, created_at=NOW, available_at=NOW)
    session = Mock()
    session.get.return_value = previous
    session.scalar.return_value = current
    enqueue = Mock(return_value=replacement)
    monkeypatch.setattr(services, "request_ingestion", enqueue)
    assert services.retry_job(session, previous.id) is replacement
    assert previous.status == "failed" and previous.error == "Upstream unavailable"
    enqueue.assert_called_once_with(session, current)


@pytest.mark.parametrize("failure", ["job", "source"])
def test_adaptive_cooldown_preserves_delayed_retry_and_publisher_failure_backoff(
    monkeypatch, failure
):
    settings = configuration(adaptive_source_polling_enabled=True)
    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "utcnow", lambda: NOW)
    current = source(
        approval="approved",
        polling_mode="adaptive",
        poll_interval_seconds=1800,
        consecutive_failures=int(failure == "source"),
    )
    pending = job(
        IngestionJob,
        source_id=current.id,
        attempts=int(failure == "job"),
        available_at=NOW + timedelta(minutes=2),
        dispatched_at=NOW - timedelta(minutes=1),
    )
    session = Mock()
    session.scalar.return_value = pending
    session.get.return_value = current
    before = (pending.available_at, pending.dispatched_at, pending.attempts)
    with pytest.raises(OperationConflict, match="preserves retry cooldowns"):
        services.prepare_immediate_dispatch(session, pending.id)
    assert before == (pending.available_at, pending.dispatched_at, pending.attempts)
    session.flush.assert_not_called()
