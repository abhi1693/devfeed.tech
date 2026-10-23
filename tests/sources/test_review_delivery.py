"""Source review delivery and worker handoffs preserve current lifecycle facts."""

import copy
import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_aggregator import solver_jobs, source_tasks
from devfeed_core import config, jobs, source_enrichment, source_lifecycle, source_notifications
from devfeed_core import source_relevance as relevance
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import SourceDiscoveryJob, SourceReview, UserAccount
from devfeed_core.notifications import notification_subscriber_id
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import OperationConflict
from sqlalchemy.orm import Session

import tests.sources.test_lifecycle_commands as lifecycle_fixtures
import tests.sources.test_worker_source_contracts_unit as worker_fixtures
from tests.sources.test_lifecycle_commands import advance, candidate, derive
from tests.support.source_readiness import NOW, assessment, configuration, job, profile, source


def assessed(current, verdict):
    inspected = profile(
        current,
        created_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(minutes=90),
    )
    sample = [
        {
            "index": index,
            "title": f"Software engineering architecture practices {index}",
            "summary": "",
        }
        for index in range(3)
    ]
    inspected.result["sample"] = sample
    reviewed = assessment(
        current,
        inspected,
        created_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(hours=1),
        finished_at=NOW - timedelta(minutes=30),
    )
    output = relevance.SourceRelevance(
        relevance=verdict,
        confidence=0.98,
        reason=(
            "The sampled entries establish the publication's software engineering focus."
            if verdict == "relevant"
            else "The supplied titles lack enough context to establish the editorial focus."
        ),
        entries=[
            {"index": row["index"], "relevance": verdict, "evidence": row["title"]}
            for row in sample
        ],
    )
    current.relevance_assessment.update(
        **output.model_dump(mode="json"),
        approval_supported=relevance.approval_supported(output, sample),
        rejection_supported=relevance.rejection_supported(output, sample),
    )
    reviewed.result["output"] = copy.deepcopy(current.relevance_assessment)
    return {"profile": inspected, "assessment": reviewed}


@pytest.mark.parametrize("disabled", ["ai", "automation", "zero-limit"])
def test_pending_scan_requires_both_automation_switches_and_a_positive_budget(
    monkeypatch, disabled
):
    settings = configuration(ai_enabled=disabled != "ai", full_automation=disabled != "automation")
    if disabled == "ai":
        # Full automation enables AI at startup; the independent kill switch
        # can subsequently pause it while the scheduler remains alive.
        settings.ai_enabled = False
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    factory = Mock()
    assert (
        relevance.schedule_pending_reviews(factory, limit=0 if disabled == "zero-limit" else 2) == 0
    )
    factory.begin.assert_not_called()


@pytest.mark.parametrize(
    "mode", ["profile", "assessment", "supported", "manual", "uncertain", "lost-lock"]
)
def test_pending_scan_reconciles_only_actionable_or_supported_current_facts(monkeypatch, mode):
    settings = configuration(ai_enabled=True, full_automation=True)
    current = source(feed_url="https://publisher.example/feed")
    jobs = {}
    if mode == "assessment":
        jobs["profile"] = profile(current)
    elif mode in {"supported", "manual", "uncertain", "lost-lock"}:
        jobs = assessed(current, "uncertain" if mode == "uncertain" else "relevant")
    if mode == "manual":
        current.processing_request = {
            "id": str(uuid.uuid4()),
            "kind": "reopen_review",
            "actor": "editor",
            "requested_at": (NOW - timedelta(hours=3)).isoformat(),
            "manual_review": True,
            "required": ["profile", "branding"],
        }
    facts = derive(current, [], jobs, settings)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalars.return_value.partitions.return_value = iter([[current]])
    session.scalar.return_value = None if mode == "lost-lock" else current
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    monkeypatch.setattr(
        source_lifecycle, "source_readiness_batch", lambda _session, rows: {facts.key: facts}
    )
    reconcile = Mock()
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)
    expected = mode in {"profile", "assessment", "supported"}
    assert relevance.schedule_pending_reviews(factory) == int(expected)
    if expected:
        reconcile.assert_called_once_with(session, current, automatic=True)
    else:
        reconcile.assert_not_called()
    assert current.approval_status == "pending" and not current.enabled


def test_pending_scan_budget_is_shared_across_partitions_and_counts_only_claimed_rows(monkeypatch):
    settings = configuration(ai_enabled=True, full_automation=True)
    rows = [source(id=uuid.UUID(int=value)) for value in range(1, 5)]
    facts = {f"source:{row.id}": derive(row, [], {}, settings) for row in rows}
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalars.return_value.partitions.return_value = iter([rows[:2], rows[2:]])
    session.scalar.side_effect = [None, rows[1], rows[2]]
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    monkeypatch.setattr(
        source_lifecycle,
        "source_readiness_batch",
        lambda _session, batch: {f"source:{row.id}": facts[f"source:{row.id}"] for row in batch},
    )
    reconcile = Mock()
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)
    assert relevance.schedule_pending_reviews(factory, limit=2) == 2
    assert [call.args[1] for call in reconcile.call_args_list] == rows[1:3]
    assert session.scalar.call_count == 3


@pytest.mark.parametrize(
    "case",
    [
        "disabled",
        "no-environment",
        "unverified",
        "malformed-user",
        "deleted-user",
        "approved",
        "rejected",
        "long-note",
    ],
)
def test_review_receipt_is_private_to_a_verified_existing_submitter(monkeypatch, case):
    user_id = uuid.uuid4()
    current = source(
        feed_url="https://publisher.example/feed",
        submitted_by={"verified": True, "user_id": str(user_id)},
    )
    current.name = "Engineering publication"
    decision = "approved" if case == "approved" else "rejected"
    current.approval_status = decision
    current.enabled = decision == "approved"
    decision_body = SourceDecision(
        decision=decision,
        actor="editor",
        note=(
            ("Detailed editorial reason. " * 40)[:1000]
            if case == "long-note"
            else "Unrelated publication focus"
        ),
    )
    current.reviewed_at, current.reviewed_by, current.review_note = (
        NOW,
        "editor",
        decision_body.note,
    )
    review = SourceReview(
        id=uuid.uuid4(),
        source_id=current.id,
        decision=decision,
        actor="editor",
        note=decision_body.note,
        created_at=NOW,
    )
    settings = configuration(
        notifications_enabled=case != "disabled",
        chimely_user_environment="" if case == "no-environment" else "reader-inbox",
    )
    if case == "unverified":
        current.submitted_by["verified"] = False
    elif case == "malformed-user":
        current.submitted_by["user_id"] = "not-a-uuid"
    user = UserAccount(
        id=user_id, issuer="https://identity.example", subject="reader", organization_id="team"
    )
    session, enqueue = Mock(), Mock()
    session.get.return_value = None if case == "deleted-user" else user
    monkeypatch.setattr(source_notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(source_notifications, "enqueue_notification", enqueue)
    source_notifications.notify_source_review(session, current, review)
    if case not in {"approved", "rejected", "long-note"}:
        enqueue.assert_not_called()
        if case != "deleted-user":
            session.get.assert_not_called()
        return
    session.get.assert_called_once_with(UserAccount, user_id)
    args, kwargs = enqueue.call_args
    assert args[:2] == (session, f"source-review:{review.id}")
    message = args[2]
    assert kwargs == {
        "audience": "user",
        "subscriber_id": notification_subscriber_id(
            audience="user",
            issuer=user.issuer,
            subject=user.subject,
            organization_id=user.organization_id,
        ),
    }
    assert message.category == f"sources.{decision}"
    assert message.action_url == (
        f"/sources/{current.id}" if decision == "approved" else "/sources/suggest"
    )
    assert message.severity == ("success" if decision == "approved" else "warning")
    assert current.name in message.body and decision in message.body
    if case == "approved":
        assert "Reason:" not in message.body
    elif case == "long-note":
        assert len(message.body) == 1000 and message.body.endswith("…")
    else:
        assert review.note in message.body
    assert user.subject not in message.body and user.issuer not in message.body


def test_long_source_passages_offer_bounded_verbatim_evidence_without_changing_the_sample():
    passage = "Software product architecture " * 30
    entry = {"index": 0, "title": "Short", "summary": passage}
    options = relevance.evidence_options(entry)
    assert options == [" ".join(passage.split())[:500]]
    assert len(options[0]) == 500 and options[0] in passage


readiness_runtime = lifecycle_fixtures.runtime


source_worker = worker_fixtures.runtime


def test_duplicate_source_delivery_does_not_fetch_or_apply_work_held_by_another_owner(
    source_worker,
):
    source_worker.claim.return_value = None
    before = copy.deepcopy(
        (source_worker.job.status, source_worker.job.attempts, source_worker.job.lease_token)
    )

    source_tasks.enrich_source(str(source_worker.job.id))

    source_worker.lookup.assert_not_called()
    source_worker.owned.assert_not_called()
    assert (
        source_worker.job.status,
        source_worker.job.attempts,
        source_worker.job.lease_token,
    ) == before


def test_unclassified_transport_failure_keeps_safe_retry_state_and_its_attempt_budget(
    source_worker,
):
    source_worker.lookup.side_effect = FeedError(
        "private resolver details", reason="transport_error", retryable=True
    )

    source_tasks.enrich_source(str(source_worker.job.id))

    assert source_worker.job.status == "queued" and source_worker.job.attempts == 1
    assert source_worker.job.available_at > NOW and source_worker.job.lease_token is None
    assert source_worker.job.error == "Source enrichment failed: transport_error"
    assert source_worker.source.metadata_error == source_worker.job.error
    source_worker.reconcile.assert_not_called()


def test_browser_challenge_handoff_uses_the_real_durable_solver_receipt_without_spending_a_retry(
    source_worker, monkeypatch
):
    monkeypatch.setattr(
        solver_jobs,
        "get_settings",
        lambda: configuration(solver_queue_enabled=True, solver_services=[]),
    )
    monkeypatch.setattr(solver_jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(source_tasks, "defer_to_solver", solver_jobs.defer_to_solver)
    source_worker.lookup.side_effect = FeedError(
        "private challenge body", reason="browser_challenge", status=403, retryable=True
    )

    source_tasks.enrich_source(str(source_worker.job.id))

    row = source_worker.job
    assert row.status == "queued" and row.requires_solver and row.attempts == 0
    assert row.available_at == NOW and row.dispatched_at is None and row.finished_at is None
    assert row.lease_token is row.lease_until is None
    assert "browser verification" in row.error and "private challenge" not in row.error
    assert source_worker.source.metadata_error == row.error
    source_worker.reconcile.assert_not_called()


def test_source_less_candidate_with_active_discovery_cannot_advance_into_other_work(
    readiness_runtime,
):
    value = readiness_runtime
    value.source, value.candidates = None, [candidate()]
    active = job(
        SourceDiscoveryJob,
        candidate_id=value.candidates[0].id,
        stage="discover",
        result={"background": True},
    )
    value.tasks["discovery"] = active
    assert value.facts().readiness.next_action is None
    before = copy.deepcopy((active.status, active.attempts, active.result))

    with pytest.raises(OperationConflict):
        advance(value)

    assert (active.status, active.attempts, active.result) == before
    assert not any(command.called for command in value.commands)


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


def test_completed_uncertain_assessment_keeps_automatic_source_admission_pending(readiness_runtime):
    readiness_runtime.settings = configuration(ai_enabled=True, full_automation=True)
    readiness_runtime.tasks.update(assessed(readiness_runtime.source, "uncertain"))
    before = readiness_runtime.facts()
    assert before.readiness.checks["assessment"].status == "complete"
    assert readiness_runtime.source.relevance_assessment["approval_supported"] is False
    assert readiness_runtime.source.relevance_assessment["rejection_supported"] is False

    after = source_lifecycle.reconcile_source_processing(
        readiness_runtime.session, readiness_runtime.source
    )

    assert after.readiness.stage == "review" and after.readiness.can_approve
    assert (
        readiness_runtime.source.approval_status == "pending"
        and not readiness_runtime.source.enabled
    )
    assert not any(command.called for command in readiness_runtime.commands)
