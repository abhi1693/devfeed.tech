"""Source orchestration follows real readiness and current admission authority.

Supplied rows and child commands are repository/service boundaries. These tests do
not evaluate SQL selection, locking, uniqueness, or transaction behavior.
"""

import copy
import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

from devfeed_core import config, jobs, services, source_enrichment, source_lifecycle
from devfeed_core.models import IngestionJob, SourceEnrichmentJob
from devfeed_core.schemas import SourcePatch
from sqlalchemy.orm import Session
from test_runtime_source_review_delivery_unit import assessed
from test_source_lifecycle_commands_unit import advance, candidate
from test_source_lifecycle_commands_unit import runtime as runtime
from test_source_readiness_unit import NOW, configuration, source


def test_initial_profile_request_captures_current_identity_without_approving_the_source():
    current = source()
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, None]

    emitted = source_enrichment.request_enrichment(session, current.id)

    assert isinstance(emitted, SourceEnrichmentJob) and emitted.source_id == current.id
    assert emitted.operation == "profile" and emitted.result == {
        "input": {
            "feed_url": current.feed_url,
            "source_type": "publisher",
            "website_url": current.website_url,
        }
    }
    captured = copy.deepcopy(emitted.result)
    current.website_url = "https://publisher.example/revised/"
    assert emitted.result == captured
    assert current.approval_status == "pending" and not current.enabled
    session.add.assert_called_once_with(emitted)
    session.flush.assert_called_once_with()


def test_enabled_ai_claim_runs_the_admitted_profile_sample_with_a_fresh_lease(monkeypatch):
    settings = configuration(ai_enabled=True)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(source_enrichment, "utcnow", lambda: NOW)
    current = source()
    completed = assessed(current, "uncertain")["profile"]
    completed.result["sample_hash"] = hashlib.sha256(
        json.dumps(completed.result["sample"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, None]
    admitted = source_enrichment.request_relevance(session, current.id, profile=completed)
    # The next repository read supplies the committed job with ordinary ORM defaults.
    admitted.id = uuid.uuid4()
    admitted.status, admitted.attempts = "queued", 0
    admitted.created_at = admitted.available_at = NOW - timedelta(seconds=10)
    before = copy.deepcopy(admitted.result)
    session.scalar.side_effect = [current, admitted]

    claimed, owner = source_enrichment.claim_enrichment(session, admitted.id)

    assert claimed is admitted and owner is current
    assert admitted.status == "running" and admitted.attempts == 1
    assert isinstance(admitted.lease_token, uuid.UUID)
    assert admitted.lease_until == NOW + timedelta(seconds=jobs.LEASE_SECONDS)
    assert admitted.result == before
    assert admitted.result["sample"] == completed.result["sample"]
    assert admitted.result["sample_hash"] == completed.result["sample_hash"]
    assert current.approval_status == "pending" and not current.enabled


def test_operator_pause_retires_a_queued_ingestion_without_counting_an_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    current = source(
        approval="approved",
        consecutive_failures=2,
        next_fetch_at=NOW + timedelta(hours=1),
        last_error="Previous upstream failure",
    )
    queued = IngestionJob(
        id=uuid.uuid4(),
        source_id=current.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(minutes=5),
        available_at=NOW - timedelta(minutes=4),
    )
    session = Mock(spec=Session)
    session.scalar.return_value = current
    services.update_source(session, current.id, SourcePatch(enabled=False))
    assert current.approval_status == "approved" and not current.enabled
    before = (current.consecutive_failures, current.next_fetch_at, current.last_error)
    session.scalar.side_effect = [current, queued]

    assert jobs.claim_job(session, queued.id) is None

    assert queued.status == "failed" and queued.error == "Source is disabled"
    assert queued.finished_at == NOW and queued.attempts == 0
    assert queued.lease_token is queued.lease_until is None
    assert (current.consecutive_failures, current.next_fetch_at, current.last_error) == before


def test_automatic_reconciliation_admits_missing_profile_without_granting_polling(runtime):
    before = runtime.facts()
    assert before.readiness.next_action == "profile"

    after = source_lifecycle.reconcile_source_processing(runtime.session, runtime.source)

    runtime.profile.assert_called_once_with(runtime.session, runtime.source.id)
    assert after.readiness.checks["profile"].status == "queued"
    assert after.readiness.next_action is None and after.snapshot != before.snapshot
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled


def test_completed_uncertain_assessment_keeps_automatic_source_admission_pending(runtime):
    runtime.settings = configuration(ai_enabled=True, full_automation=True)
    runtime.tasks.update(assessed(runtime.source, "uncertain"))
    before = runtime.facts()
    assert before.readiness.checks["assessment"].status == "complete"
    assert runtime.source.relevance_assessment["approval_supported"] is False
    assert runtime.source.relevance_assessment["rejection_supported"] is False

    after = source_lifecycle.reconcile_source_processing(runtime.session, runtime.source)

    assert after.readiness.stage == "review" and after.readiness.can_approve
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
    assert not any(command.called for command in runtime.commands)


def test_website_only_source_advance_delegates_to_discovery_before_profile_admission(runtime):
    runtime.source.feed_url = None

    def review(session, current):
        assert session is runtime.session and current is runtime.source
        runtime.candidates.append(candidate(current, status="pending"))
        runtime.discovery(session, runtime.candidates[0].id, background=True)
        return None

    runtime.review.side_effect = review
    before = runtime.facts()
    assert before.readiness.next_action == "discovery" and before.candidate is None

    after = advance(runtime)

    runtime.review.assert_called_once_with(runtime.session, runtime.source)
    runtime.profile.assert_not_called()
    assert after.readiness.stage == "discovery"
    assert after.readiness.checks["discovery"].status == "queued"
    assert runtime.source.feed_url is None
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
