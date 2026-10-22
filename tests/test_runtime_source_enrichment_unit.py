"""Source work commands preserve current evidence, active leases, and review authority.

Repository reads are supplied records. SQL predicates, locking, uniqueness and
transaction behavior are integration contracts, not evaluated by these tests.
"""

import copy
import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import discovery, services, source_notifications
from devfeed_core import source_enrichment as enrichment
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.models import SourceCandidate, SourceEnrichmentJob
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_relevance import VERSION
from sqlalchemy.orm import Session
from test_source_readiness_unit import NOW, configuration, profile, source


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(enrichment, "utcnow", lambda: NOW)


def pending_profile(current, **changes):
    return profile(
        current,
        status="queued",
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        **changes,
    )


@pytest.mark.parametrize("existing", [False, True])
def test_website_review_uses_discovery_without_creating_an_approved_source(monkeypatch, existing):
    current = source(feed_url=None)
    candidate = (
        SourceCandidate(
            id=uuid.uuid4(),
            source_id=current.id,
            name=current.name,
            identity_url=current.website_url,
            status="pending",
            approval_status="pending",
        )
        if existing
        else None
    )
    session = Mock(spec=Session)
    session.scalar.return_value = candidate
    enqueue = Mock()
    monkeypatch.setattr(discovery, "enqueue", enqueue)

    assert enrichment.request_source_review(session, current) is None

    if existing:
        session.add.assert_not_called()
    else:
        candidate = session.add.call_args.args[0]
        assert isinstance(candidate, SourceCandidate) and candidate.source_id == current.id
        assert candidate.identity_url == current.website_url
    enqueue.assert_called_once_with(session, candidate.id, background=True)
    assert current.approval_status == "pending" and not current.enabled


@pytest.mark.parametrize("invalid", ["rejected", "no-website"])
def test_review_without_a_feed_requires_pending_website_authority(invalid):
    current = source(approval="rejected" if invalid == "rejected" else "pending", feed_url=None)
    if invalid == "no-website":
        current.website_url = None
    session = Mock(spec=Session)

    with pytest.raises(OperationConflict, match="pending source with a website"):
        enrichment.request_source_review(session, current)

    session.scalar.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("supersede", [False, True])
def test_profile_request_reuses_active_work_or_retires_it_for_an_explicit_new_input(supersede):
    current = source()
    old = pending_profile(current)
    before = copy.deepcopy(old.result)
    if supersede:
        current.website_url = "https://publisher.example/new-site"
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, old]

    chosen = enrichment.request_enrichment(session, current.id, supersede=supersede)

    if supersede:
        assert (
            chosen is not old and chosen.source_id == current.id and chosen.operation == "profile"
        )
        assert chosen.result == {"input": enrichment.source_identity(current)}
        assert old.status == "failed" and old.finished_at == NOW and old.lease_token is None
        assert old.result == before
        session.add.assert_called_once_with(chosen)
    else:
        assert chosen is old and old.status == "queued" and old.result == before
        session.add.assert_not_called()


@pytest.mark.parametrize("problem", ["missing", "rejected", "undiscovered"])
def test_profile_admission_requires_an_existing_nonrejected_discovered_source(problem):
    current = source(
        approval="rejected" if problem == "rejected" else "pending",
        feed_url=None if problem == "undiscovered" else "https://publisher.example/feed",
    )
    session = Mock(spec=Session)
    session.scalar.return_value = None if problem == "missing" else current

    with pytest.raises(RecordNotFound if problem == "missing" else OperationConflict):
        enrichment.request_enrichment(session, current.id)

    session.add.assert_not_called()


@pytest.mark.parametrize("automatic", [False, True])
def test_disabled_ai_skips_automatic_assessment_but_explains_manual_refusal(monkeypatch, automatic):
    settings = configuration(ai_enabled=False)
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: settings)
    current, session = source(), Mock(spec=Session)
    session.scalar.return_value = current

    if automatic:
        assert enrichment.request_relevance(session, current.id, automatic=True) is None
    else:
        with pytest.raises(OperationConflict, match="Configure AI"):
            enrichment.request_relevance(session, current.id)
    session.add.assert_not_called()


def test_automatic_assessment_does_not_reopen_an_already_approved_source(monkeypatch):
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: configuration(ai_enabled=True))
    current, session = source(approval="approved"), Mock(spec=Session)
    session.scalar.return_value = current

    assert enrichment.request_relevance(session, current.id, automatic=True) is None

    session.add.assert_not_called()


@pytest.mark.parametrize("provided", [False, True])
def test_assessment_request_carries_the_current_profile_sample_and_policy_receipt(
    monkeypatch, provided
):
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: configuration(ai_enabled=True))
    current = source()
    completed = profile(current)
    sample = [{"title": "Python routing", "summary": "Application routing guidance."}]
    completed.result.update(
        sample=sample,
        sample_hash=hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest(),
    )
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, None, *([] if provided else [completed])]

    queued = enrichment.request_relevance(
        session, current.id, **({"profile": completed} if provided else {})
    )

    assert isinstance(queued, SourceEnrichmentJob) and queued.source_id == current.id
    assert queued.operation == "relevance" and queued.result == {
        "input": enrichment.source_identity(current),
        "profile_job_id": str(completed.id),
        "sample": completed.result["sample"],
        "sample_hash": completed.result["sample_hash"],
        "policy_version": VERSION,
    }
    assert current.approval_status == "pending" and not current.enabled
    session.add.assert_called_once_with(queued)


def test_assessment_cannot_consume_profile_evidence_from_the_previous_website(monkeypatch):
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: configuration(ai_enabled=True))
    current = source()
    completed = profile(current)
    current.website_url = "https://publisher.example/changed"
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, None]

    with pytest.raises(OperationConflict, match="current source profile"):
        enrichment.request_relevance(session, current.id, profile=completed)

    session.add.assert_not_called()


@pytest.mark.parametrize("operation", ["profile", "legacy"])
def test_claim_starts_current_profile_work_with_a_new_lease_and_migrates_legacy_deliveries(
    operation,
):
    current = source()
    queued = pending_profile(current)
    queued.operation = operation
    if operation == "legacy":
        queued.result = {"old_combined_operation": True}
    before = copy.deepcopy(queued.result)
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, queued]

    claimed, selected = enrichment.claim_enrichment(session, queued.id)

    assert claimed is queued and selected is current
    assert queued.status == "running" and queued.attempts == 1
    assert isinstance(queued.lease_token, uuid.UUID) and queued.lease_until == NOW + timedelta(
        seconds=LEASE_SECONDS
    )
    assert queued.operation == "profile"
    assert queued.result == (
        {"input": enrichment.source_identity(current)} if operation == "legacy" else before
    )


def test_queued_assessment_obeys_the_ai_kill_switch_without_consuming_an_attempt(monkeypatch):
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: configuration(ai_enabled=False))
    current = source()
    queued = pending_profile(current)
    queued.operation = "relevance"
    completed = profile(current)
    sample = [{"title": "Python routing", "summary": "Routing application requests."}]
    queued.result = {
        "input": enrichment.source_identity(current),
        "profile_job_id": str(completed.id),
        "sample": sample,
        "sample_hash": hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest(),
        "policy_version": VERSION,
    }
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, queued]

    assert enrichment.claim_enrichment(session, queued.id) is None

    assert queued.status == "queued" and queued.attempts == 0 and queued.lease_token is None


@pytest.mark.parametrize("state", ["pending", "polling", "paused-retry", "paused"])
def test_stale_claim_requeues_only_pending_or_explicitly_requested_source_work(monkeypatch, state):
    current = source(approval="pending" if state == "pending" else "approved")
    queued = pending_profile(current)
    current.website_url = "https://publisher.example/updated"
    if state in {"paused", "paused-retry"}:
        current.enabled = False
    if state == "paused-retry":
        current.processing_request = {
            "kind": "upstream_retry",
            "id": str(uuid.uuid4()),
            "target": "profile",
            "requested_at": NOW.isoformat(),
            "required": ["profile", "branding"],
        }
    session = Mock(spec=Session)
    session.scalar.side_effect = [current, queued]
    request = Mock()
    monkeypatch.setattr(enrichment, "request_enrichment", request)

    assert enrichment.claim_enrichment(session, queued.id) is None

    assert queued.status == "failed" and queued.error == "Source input changed"
    assert queued.finished_at == NOW and queued.attempts == 0
    if state in {"pending", "paused-retry"}:
        request.assert_called_once_with(session, current.id)
    else:
        request.assert_not_called()


def test_actual_rejection_transition_leaves_delivery_to_be_retired_by_the_claim_guard(monkeypatch):
    current = source()
    queued = pending_profile(current)
    session = Mock(spec=Session)
    session.scalar.return_value = current
    monkeypatch.setattr(source_notifications, "notify_source_review", Mock())
    services.review_source(
        session,
        current.id,
        SourceDecision(decision="rejected", actor="editor", note="Not a developer publication"),
    )
    assert queued.status == "queued" and current.approval_status == "rejected"
    session.scalar.side_effect = [current, queued]

    assert enrichment.claim_enrichment(session, queued.id) is None

    assert queued.status == "failed" and queued.error == "Source was rejected"
    assert queued.lease_token is None and queued.finished_at == NOW


@pytest.mark.parametrize("status", ["running", "failed", "succeeded"])
def test_immediate_profile_dispatch_rejects_nonqueued_work_without_changing_its_state(status):
    current = source()
    existing = profile(current, status=status)
    session = Mock(spec=Session)
    session.scalar.return_value = existing
    before = (existing.status, existing.attempts, existing.lease_token, existing.lease_until)

    with pytest.raises(OperationConflict, match="Only queued"):
        enrichment.prepare_enrichment_dispatch(session, existing.id)

    assert (
        existing.status,
        existing.attempts,
        existing.lease_token,
        existing.lease_until,
    ) == before
    session.get.assert_not_called()


def test_immediate_dispatch_releases_a_queued_provider_delay_without_replacing_the_job():
    current = source()
    queued = profile(
        current,
        status="queued",
        available_at=NOW + timedelta(minutes=10),
        dispatched_at=NOW - timedelta(minutes=1),
    )
    session = Mock(spec=Session)
    session.scalar.return_value, session.get.return_value = queued, current

    assert enrichment.prepare_enrichment_dispatch(session, queued.id) is queued

    assert queued.available_at == NOW and queued.dispatched_at is None
    assert queued.status == "queued" and queued.attempts == 0
    session.flush.assert_called_once()


def test_profile_application_only_fills_gaps_that_remain_unchanged_since_claim():
    current = source()
    current.description = current.logo_url = current.language = None
    original = {field: getattr(current, field) for field in enrichment.PROFILE_FIELDS}
    original["website_url"] = current.website_url
    current.language = "en"  # An editor filled this field while discovery was running.
    candidates = {
        "description": "Publisher engineering notes.",
        "logo_url": "https://publisher.example/logo.png",
        "language": "fr",
    }
    assert enrichment.fill_profile(current, candidates, original) == ["description", "logo_url"]
    assert (
        current.description == candidates["description"]
        and current.logo_url == candidates["logo_url"]
    )
    assert current.language == "en" and current.approval_status == "pending" and not current.enabled


def test_profile_application_discards_branding_after_an_editor_changes_the_website():
    current = source(description=None, logo_url=None)
    original = {field: getattr(current, field) for field in enrichment.PROFILE_FIELDS}
    current.website_url = "https://publisher.example/new-owner"
    assert (
        enrichment.fill_profile(
            current, {"logo_url": "https://publisher.example/old-logo.png"}, original
        )
        == []
    )
    assert current.logo_url is None
