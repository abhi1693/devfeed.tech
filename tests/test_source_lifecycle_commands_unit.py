"""Source command policy over scoped facts; SQL locking/admission have integration tests."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import (
    discovery,
    jobs,
    source_enrichment,
    source_lifecycle,
    source_logos,
    source_relevance,
)
from devfeed_core.models import (
    ArticleImageJob,
    IngestionJob,
    SourceCandidate,
    SourceDiscoveryJob,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_lifecycle import SourceLifecycleFacts
from test_source_readiness_unit import NOW, assessment, configuration, job, profile, source

ACTOR = "authenticated-editor"


def candidate(current=None, **changes):
    return SourceCandidate(
        **{
            "id": uuid.uuid4(),
            "identity_url": "https://publisher.example/",
            "name": "Publisher",
            "source_id": current.id if current else None,
            "status": "linked" if current else "pending",
            "approval_status": "pending",
            "created_at": NOW - timedelta(days=2),
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def derive(current, candidates, tasks, settings):
    value = SourceLifecycleFacts(
        key=f"source:{current.id}" if current else f"candidate:{candidates[0].id}",
        source=current,
        candidates=list(candidates),
        jobs=dict(tasks),
    )
    value.readiness = source_lifecycle.derive_source_readiness(
        current,
        candidates=list(candidates),
        jobs=tasks,
        settings=settings,
        now=NOW,
    )
    value.snapshot = source_lifecycle.source_snapshot_token(value)
    return value


@pytest.fixture
def runtime(monkeypatch):
    value = SimpleNamespace(
        source=source(),
        candidates=[],
        tasks=dict.fromkeys(source_lifecycle.TARGETS),
        settings=configuration(),
        session=Mock(),
    )
    monkeypatch.setattr(source_lifecycle, "utcnow", lambda: NOW)
    monkeypatch.setattr(source_lifecycle, "get_settings", lambda: value.settings)

    def read(session, current, **_):
        assert session is value.session
        return derive(current, value.candidates, value.tasks, value.settings)

    def batch(session, sources, candidates=None, **_):
        assert session is value.session
        return {
            f"candidate:{row.id}": derive(None, [row], value.tasks, value.settings)
            for row in candidates or []
        }

    monkeypatch.setattr(source_lifecycle, "source_readiness", read)
    monkeypatch.setattr(source_lifecycle, "source_readiness_batch", batch)
    # Primary-key and linked-row reads only: no WHERE or lock evaluation.
    value.session.scalar.side_effect = lambda _: value.source or value.candidates[0]
    value.session.scalars.side_effect = lambda _: SimpleNamespace(all=lambda: value.candidates)

    def enqueue_profile(session, identifier):
        assert session is value.session and identifier == value.source.id
        queued = profile(value.source, status="queued", created_at=NOW, available_at=NOW)
        value.tasks["profile"] = queued
        return queued

    def enqueue_assessment(session, identifier, **options):
        assert session is value.session and identifier == value.source.id
        assert options["profile"] is value.tasks["profile"]
        queued = assessment(
            value.source, options["profile"], status="queued", created_at=NOW, available_at=NOW
        )
        value.tasks["assessment"] = queued
        return queued

    def enqueue_logo(session, identifier, **options):
        assert session is value.session and identifier == value.source.id
        queued = job(
            ArticleImageJob,
            source_id=identifier,
            operation="source-logo-refresh" if options.get("refresh") else "source-logo",
            image_url=value.source.logo_url,
            storage={},
            created_at=NOW,
            available_at=NOW,
        )
        value.tasks["branding"] = queued
        return queued

    def enqueue_ingestion(session, current):
        assert session is value.session and current is value.source
        queued = job(IngestionJob, source_id=current.id, created_at=NOW, available_at=NOW)
        value.tasks["ingestion"] = queued
        return queued

    def enqueue_discovery(session, identifier, **options):
        assert session is value.session and identifier == value.candidates[0].id
        queued = job(
            SourceDiscoveryJob,
            candidate_id=identifier,
            stage="discover",
            result={},
            created_at=NOW,
            available_at=NOW,
        )
        value.tasks["discovery"] = queued
        return queued

    value.profile = Mock(side_effect=enqueue_profile)
    value.assessment = Mock(side_effect=enqueue_assessment)
    value.logo = Mock(side_effect=enqueue_logo)
    value.ingestion = Mock(side_effect=enqueue_ingestion)
    value.discovery = Mock(side_effect=enqueue_discovery)
    value.review = Mock(side_effect=lambda session, current: enqueue_profile(session, current.id))
    monkeypatch.setattr(source_enrichment, "request_enrichment", value.profile)
    monkeypatch.setattr(source_enrichment, "request_relevance", value.assessment)
    monkeypatch.setattr(source_enrichment, "request_source_review", value.review)
    monkeypatch.setattr(source_logos, "request_source_logo", value.logo)
    monkeypatch.setattr(jobs, "request_ingestion", value.ingestion)
    monkeypatch.setattr(discovery, "enqueue", value.discovery)
    value.facts = lambda: derive(value.source, value.candidates, value.tasks, value.settings)
    value.commands = [
        value.profile,
        value.assessment,
        value.logo,
        value.ingestion,
        value.discovery,
        value.review,
    ]
    return value


def advance(runtime, *, expected=None):
    facts = runtime.facts()
    kind = "source" if runtime.source else "candidate"
    identifier = runtime.source.id if runtime.source else runtime.candidates[0].id
    return source_lifecycle.advance_source_processing(
        runtime.session, kind, identifier, expected or facts.snapshot, actor=ACTOR
    )


def retry(runtime, target, *, expected=None, job_id=None):
    facts = runtime.facts()
    previous = runtime.tasks[target]
    kind = "source" if runtime.source else "candidate"
    identifier = runtime.source.id if runtime.source else runtime.candidates[0].id
    return source_lifecycle.retry_source_processing(
        runtime.session,
        kind,
        identifier,
        expected or facts.snapshot,
        target,
        job_id if job_id is not None else previous.id if previous else None,
        actor=ACTOR,
    )


def test_profile_advance_starts_only_missing_work_and_returns_current_readiness(runtime):
    before = runtime.facts()
    assert before.readiness.next_action == "profile"
    result = advance(runtime)
    assert result.readiness.checks["profile"].job_id == runtime.tasks["profile"].id
    assert (
        result.readiness.checks["profile"].status == "queued" and result.snapshot != before.snapshot
    )
    assert runtime.profile.call_count == 1
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled


@pytest.mark.parametrize("command", ["advance", "retry"])
def test_stale_source_snapshot_never_queues_work_or_changes_editorial_state(runtime, command):
    before = copy.deepcopy(runtime.source.processing_request)
    with pytest.raises(OperationConflict, match="Source processing changed"):
        advance(runtime, expected="0" * 64) if command == "advance" else retry(
            runtime, "profile", expected="0" * 64
        )
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
    assert runtime.source.processing_request == before
    assert not any(operation.called for operation in runtime.commands)


@pytest.mark.parametrize("kind", ["source", "candidate"])
def test_missing_items_fail_before_any_processing_command(runtime, kind):
    runtime.session.scalar.side_effect = None
    runtime.session.scalar.return_value = None
    with pytest.raises(RecordNotFound):
        source_lifecycle.advance_source_processing(runtime.session, kind, uuid.uuid4(), "0" * 64)
    assert not any(operation.called for operation in runtime.commands)


def test_unknown_item_kind_is_rejected_without_repository_reads(runtime):
    with pytest.raises(OperationConflict, match="Unknown source lifecycle item"):
        source_lifecycle.advance_source_processing(
            runtime.session, "article", uuid.uuid4(), "0" * 64
        )
    runtime.session.scalar.assert_not_called()


def test_linked_candidate_requires_reloading_its_source_card(runtime):
    row = candidate(runtime.source)
    runtime.session.scalar.side_effect = None
    runtime.session.scalar.return_value = row
    with pytest.raises(OperationConflict, match="linked to a source"):
        source_lifecycle.advance_source_processing(runtime.session, "candidate", row.id, "0" * 64)
    assert not any(operation.called for operation in runtime.commands)


def test_discovery_advance_keeps_candidate_pending_without_creating_an_approved_source(runtime):
    runtime.source, runtime.candidates = None, [candidate()]
    result = advance(runtime)
    assert result.source is None and runtime.candidates[0].status == "pending"
    assert result.readiness.checks["discovery"].status == "queued"
    runtime.discovery.assert_called_once_with(
        runtime.session, runtime.candidates[0].id, background=True
    )


def test_unresolved_discovery_can_be_retried_with_its_exact_previous_job(runtime):
    runtime.source, runtime.candidates = None, [candidate(status="unresolved")]
    runtime.tasks["discovery"] = job(
        SourceDiscoveryJob,
        candidate_id=runtime.candidates[0].id,
        stage="discover",
        status="failed",
        result={},
    )
    result = retry(runtime, "discovery")
    assert result.readiness.checks["discovery"].status == "queued"
    assert runtime.discovery.call_count == 1


def test_selected_feed_handoff_uses_the_discovered_source_without_another_crawl(
    runtime, monkeypatch
):
    current = runtime.source
    row = candidate(status="ready", selected_feed_id=uuid.uuid4())
    runtime.source, runtime.candidates = None, [row]

    def attach(session, selected):
        assert session is runtime.session and selected is row
        row.source_id = current.id
        runtime.source = current
        return current

    handoff = Mock()
    monkeypatch.setattr(discovery, "attach_source", attach)
    monkeypatch.setattr(discovery, "handoff", handoff)
    result = advance(runtime)
    assert result.source is current and current.approval_status == "pending"
    handoff.assert_called_once_with(runtime.session, row)
    runtime.discovery.assert_not_called()


@pytest.mark.parametrize("has_feed", [False, True])
def test_reopening_a_rejection_records_manual_review_intent_and_never_enables_polling(
    runtime, has_feed
):
    runtime.source = source(
        approval="rejected",
        feed_url="https://publisher.example/feed" if has_feed else None,
        reviewed_at=NOW - timedelta(days=1),
        reviewed_by="editor",
        review_note="Insufficient relevance",
    )
    if not has_feed:
        runtime.candidates = [candidate(runtime.source, approval_status="rejected")]
    result = advance(runtime)
    current = runtime.source
    assert current.approval_status == "pending" and not current.enabled
    intent = current.processing_request
    assert intent["kind"] == "reopen_review" and intent["actor"] == ACTOR
    assert intent["manual_review"] and intent["required"] == ["profile", "branding"]
    assert intent["requested_at"] == NOW.isoformat() and uuid.UUID(intent["id"])
    assert result.source is current
    if has_feed:
        assert runtime.tasks["profile"].result["source_pipeline_request"] == intent
        runtime.review.assert_called_once_with(runtime.session, current)
    else:
        assert runtime.candidates[0].approval_status == runtime.candidates[0].status == "pending"
        assert runtime.discovery.call_count == 1
        runtime.review.assert_not_called()


def test_completed_profile_can_request_required_assessment_without_auto_approving(runtime):
    runtime.settings = configuration(ai_enabled=True, full_automation=True)
    runtime.tasks["profile"] = profile(runtime.source)
    assert runtime.facts().readiness.next_action == "assessment"
    result = advance(runtime)
    assert result.readiness.checks["assessment"].status == "queued"
    runtime.assessment.assert_called_once_with(
        runtime.session, runtime.source.id, profile=runtime.tasks["profile"]
    )
    assert runtime.source.approval_status == "pending"


def test_missing_required_logo_starts_branding_and_keeps_review_blocked(runtime):
    runtime.settings = configuration(
        image_storage_enabled=True, image_public_url="https://images.example"
    )
    runtime.source.logo_url = "https://publisher.example/logo.png"
    runtime.tasks["profile"] = profile(runtime.source)
    assert runtime.facts().readiness.next_action == "branding"
    result = advance(runtime)
    assert (
        result.readiness.checks["branding"].status == "queued" and not result.readiness.can_approve
    )
    runtime.logo.assert_called_once_with(runtime.session, runtime.source.id, automatic=True)


@pytest.mark.parametrize("first_ingestion", [False, True])
def test_resume_or_initial_ingestion_preserves_approval_and_clears_settled_retry_intent(
    runtime, first_ingestion
):
    runtime.source = source(
        approval="approved",
        enabled=first_ingestion,
        last_success_at=None if first_ingestion else NOW - timedelta(days=1),
    )
    result = advance(runtime)
    assert runtime.source.enabled and runtime.source.approval_status == "approved"
    assert runtime.source.processing_request == {}
    assert result.readiness.checks["ingestion"].status == "queued"
    runtime.ingestion.assert_called_once_with(runtime.session, runtime.source)


def test_review_ready_source_has_no_implicit_approval_advance(runtime):
    runtime.tasks["profile"] = profile(runtime.source)
    assert runtime.facts().readiness.can_approve
    with pytest.raises(OperationConflict, match="No processing task is available"):
        advance(runtime)
    assert not any(operation.called for operation in runtime.commands)


@pytest.mark.parametrize("target", ["profile", "assessment", "branding", "ingestion"])
def test_approved_source_retry_preserves_approval_and_binds_receipt_to_exact_previous_work(
    runtime, target
):
    runtime.settings = configuration(
        ai_enabled=True, image_storage_enabled=True, image_public_url="https://images.example"
    )
    runtime.source = source(approval="approved", logo_url="https://publisher.example/logo.png")
    runtime.tasks["profile"] = profile(runtime.source)
    if target == "profile":
        runtime.tasks["profile"] = profile(runtime.source, status="failed")
    elif target == "assessment":
        runtime.tasks["assessment"] = assessment(
            runtime.source,
            runtime.tasks["profile"],
            status="failed",
            created_at=NOW - timedelta(seconds=45),
            available_at=NOW - timedelta(seconds=45),
            finished_at=NOW - timedelta(seconds=15),
        )
    elif target == "branding":
        runtime.tasks["branding"] = job(
            ArticleImageJob,
            source_id=runtime.source.id,
            operation="source-logo",
            image_url=runtime.source.logo_url,
            status="failed",
            storage={},
        )
    else:
        runtime.tasks["ingestion"] = job(IngestionJob, source_id=runtime.source.id, status="failed")
    previous = runtime.tasks[target]
    assert target in runtime.facts().readiness.retry_targets
    result = retry(runtime, target)
    assert result.source.approval_status == "approved" and runtime.source.enabled is (
        target == "ingestion"
    )
    queued = runtime.tasks[target]
    if target != "ingestion":
        intent = runtime.source.processing_request
        assert intent["target"] == target and intent["previous_job_id"] == str(previous.id)
        assert intent["actor"] == ACTOR and set(intent["required"]) == {
            "profile",
            "branding",
            *(["assessment"] if target == "assessment" else []),
        }
        receipt = queued.storage if target == "branding" else queued.result
        assert receipt["source_pipeline_request"] == {
            "kind": "upstream_retry",
            "target": target,
            "actor": ACTOR,
            "previous_job_id": str(previous.id),
        }
    else:
        assert runtime.source.processing_request == {}


@pytest.mark.parametrize("invalid", ["job-id", "active-work"])
def test_retry_rejects_stale_job_identity_and_current_active_work(runtime, invalid):
    if invalid == "active-work":
        runtime.tasks["profile"] = profile(runtime.source, status="running")
    with pytest.raises(OperationConflict, match="cannot be retried"):
        retry(runtime, "profile", job_id=uuid.uuid4())
    assert not any(operation.called for operation in runtime.commands)


def test_domain_refusal_to_enqueue_is_reported_as_a_conflict(runtime):
    runtime.profile.side_effect = None
    runtime.profile.return_value = None
    with pytest.raises(OperationConflict, match="No source processing task could be queued"):
        retry(runtime, "profile")


def test_pending_source_retry_does_not_create_an_approved_source_resume_request(runtime):
    result = retry(runtime, "profile")
    assert result.source.approval_status == "pending" and not result.source.enabled
    assert result.source.processing_request == {}
    assert runtime.tasks["profile"].result["source_pipeline_request"]["target"] == "profile"


@pytest.mark.parametrize("manual", [False, True])
def test_automatic_reconciliation_respects_a_manual_review_reopen(runtime, monkeypatch, manual):
    from devfeed_core import services

    runtime.settings = configuration(ai_enabled=True, full_automation=True)
    runtime.source.processing_request = (
        {
            "kind": "reopen_review",
            "id": str(uuid.uuid4()),
            "actor": ACTOR,
            "requested_at": (NOW - timedelta(minutes=10)).isoformat(),
            "manual_review": True,
            "required": ["profile", "branding"],
        }
        if manual
        else {}
    )
    runtime.tasks["profile"] = profile(
        runtime.source,
        created_at=NOW - timedelta(minutes=9),
        available_at=NOW - timedelta(minutes=9),
        finished_at=NOW - timedelta(minutes=8),
    )
    runtime.tasks["assessment"] = assessment(
        runtime.source,
        runtime.tasks["profile"],
        created_at=NOW - timedelta(minutes=7),
        available_at=NOW - timedelta(minutes=7),
        finished_at=NOW - timedelta(minutes=6),
    )
    sample = [
        {"index": index, "title": f"Python service design {index}", "summary": ""}
        for index in range(3)
    ]
    classification = source_relevance.SourceRelevance(
        relevance="relevant",
        confidence=0.98,
        reason="Engineering feed sample supports developer relevance.",
        entries=[
            {"index": entry["index"], "relevance": "relevant", "evidence": entry["title"]}
            for entry in sample
        ],
    )
    runtime.tasks["profile"].result["sample"] = sample
    output = runtime.source.relevance_assessment
    output.update(
        **classification.model_dump(mode="json"),
        approval_supported=source_relevance.approval_supported(classification, sample),
        rejection_supported=source_relevance.rejection_supported(classification, sample),
    )
    runtime.tasks["assessment"].result["output"] = copy.deepcopy(output)
    review = Mock()
    monkeypatch.setattr(services, "review_source", review)
    source_lifecycle.reconcile_source_processing(runtime.session, runtime.source)
    if manual:
        review.assert_not_called()
    else:
        assert review.call_args.args[:2] == (runtime.session, runtime.source.id)
        assert review.call_args.args[2].decision == "approved"
        assert review.call_args.args[2].actor == "devfeed:source-relevance"


def test_reconciliation_does_not_suspend_or_backfill_existing_approved_polling(runtime):
    runtime.source = source(approval="approved")
    result = source_lifecycle.reconcile_source_processing(runtime.session, runtime.source)
    assert result.source.enabled and result.source.approval_status == "approved"
    assert not any(operation.called for operation in runtime.commands)


def test_rejected_source_is_unchanged_by_automatic_reconciliation(runtime):
    runtime.source = source(
        approval="rejected",
        reviewed_at=NOW - timedelta(days=1),
        reviewed_by="editor",
        review_note="Insufficient relevance",
    )
    result = source_lifecycle.reconcile_source_processing(runtime.session, runtime.source)
    assert result.source.approval_status == "rejected" and not result.source.enabled
    assert not any(operation.called for operation in runtime.commands)
