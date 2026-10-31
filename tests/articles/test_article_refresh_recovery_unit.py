"""Durable maintenance resumes selected work after callbacks or workers disappear.

Supplied repository rows and readiness are explicit policy results. PostgreSQL
claim exclusion, guard visibility, and lock interleavings have native coverage.
"""

import logging
import re
import uuid
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import article_jobs, article_lifecycle, article_refresh, image_jobs
from devfeed_core import article_refresh_processing as recovery
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleRefresh,
)
from devfeed_core.services import OperationConflict
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


@pytest.fixture
def continuation(monkeypatch):
    public = Article(
        id=uuid.uuid4(),
        publication_status="published",
        review_status="approved",
        editorial_revision=3,
    )
    owner = ArticleRefresh(
        id=uuid.uuid4(),
        article_id=public.id,
        status="active",
        target="analysis",
        version=2,
        base_revision=3,
        payload={"private": "retained extraction"},
    )
    analysis = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=public.id,
        refresh_id=owner.id,
        status="succeeded",
        outcome="applied",
        input_hash="a" * 64,
    )
    enrichment = ArticleEnrichmentJob(
        id=uuid.uuid4(),
        article_id=public.id,
        refresh_id=owner.id,
        status="succeeded",
        outcome="enriched",
    )
    owner.analysis_job_id, owner.enrichment_job_id = analysis.id, enrichment.id
    rows = {Article: public, ArticleAnalysisJob: analysis, ArticleEnrichmentJob: enrichment}
    metadata, reader, writer = Mock(spec=Session), Mock(spec=Session), Mock(spec=Session)
    metadata.scalar.return_value = owner
    metadata.get.side_effect = lambda model, identifier: rows.get(model)
    writer.scalar.side_effect = [public, owner]
    facts = {
        "content": None,
        "enrichment": enrichment,
        "analysis": analysis,
        "discovery": None,
        "storage": None,
    }
    inputs = SimpleNamespace(
        refresh_id=owner.id,
        version=owner.version,
        public=public,
        article=public,
        facts=facts,
        taxonomy={"topics": [], "tags": []},
        input_hash=analysis.input_hash,
        snapshot={"url": "https://publisher.example/private", "text": "Private extraction"},
    )
    checks = {
        slot: article_lifecycle.ProcessingCheck(status="complete")
        for slot in ("enrichment", "analysis", "images")
    }
    readiness = article_lifecycle.ProcessingReadiness(stage="ready", state="ready", checks=checks)
    events, sessions = [], [metadata, reader]

    @contextmanager
    def phase(name, session):
        events.append(name)
        try:
            yield session
        finally:
            events.append(name + "_closed")

    factory = Mock(
        side_effect=lambda: phase(
            "metadata" if sessions[0] is metadata else "prepare", sessions.pop(0)
        )
    )
    factory.begin.side_effect = lambda: phase("write", writer)
    authority = Mock(return_value=True)
    prepare = Mock(return_value=inputs)
    policy = Mock(return_value=readiness)
    catalog_current = Mock(return_value=True)
    analysis_admission, image_admission = Mock(), Mock(return_value=object())
    promote = Mock(return_value="promoted")
    monkeypatch.setattr(article_jobs, "approved_sources", authority)
    monkeypatch.setattr(recovery, "prepare_refresh_inputs", prepare)
    monkeypatch.setattr(article_lifecycle, "derive_publication_readiness", policy)
    monkeypatch.setattr(recovery, "analysis_catalog_current", catalog_current)
    monkeypatch.setattr(recovery, "request_refresh_analysis", analysis_admission)
    monkeypatch.setattr(image_jobs, "request_image", image_admission)
    monkeypatch.setattr(article_refresh, "try_promote_refresh", promote)
    monkeypatch.setattr(recovery, "utcnow", lambda: NOW)
    return SimpleNamespace(
        public=public,
        owner=owner,
        analysis=analysis,
        enrichment=enrichment,
        metadata=metadata,
        reader=reader,
        writer=writer,
        rows=rows,
        inputs=inputs,
        readiness=readiness,
        factory=factory,
        events=events,
        authority=authority,
        prepare=prepare,
        policy=policy,
        catalog_current=catalog_current,
        analysis_admission=analysis_admission,
        image_admission=image_admission,
        promote=promote,
    )


@pytest.mark.parametrize("status", ["missing", "promoted", "superseded", "cancelled"])
def test_terminal_or_missing_owner_is_never_reprepared_or_reactivated(continuation, status):
    c = continuation
    if status == "missing":
        c.metadata.scalar.return_value = None
    else:
        c.owner.status = status
    assert recovery.resume_refresh(c.factory, c.owner.id) == status
    c.metadata.get.assert_not_called()
    c.prepare.assert_not_called()
    c.factory.begin.assert_not_called()
    c.promote.assert_not_called()


@pytest.mark.parametrize("slot", ["enrichment", "analysis", "discovery", "storage"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_current_selected_work_is_left_running_without_duplicate_admission(
    continuation, slot, status
):
    c = continuation
    if slot in {"discovery", "storage"}:
        job = ArticleImageJob(
            id=uuid.uuid4(),
            article_id=c.public.id,
            refresh_id=c.owner.id,
            operation="discover" if slot == "discovery" else "store",
            status=status,
        )
        setattr(c.owner, f"{slot}_job_id", job.id)
        c.rows[ArticleImageJob] = job
    else:
        job = c.rows[ArticleAnalysisJob if slot == "analysis" else ArticleEnrichmentJob]
        job.status = status
    assert recovery.resume_refresh(c.factory, c.owner.id) == "pending"
    c.prepare.assert_not_called()
    c.authority.assert_not_called()
    c.factory.begin.assert_not_called()
    c.analysis_admission.assert_not_called()
    c.image_admission.assert_not_called()


@pytest.mark.parametrize("reason", ["missing", "withdrawn", "unapproved", "revision", "source"])
def test_lost_public_authority_supersedes_only_after_rechecking_in_source_article_owner_order(
    continuation, reason
):
    c = continuation
    if reason == "missing":
        c.rows[Article] = None
        c.writer.scalar.side_effect = [None, c.owner]
    elif reason == "withdrawn":
        c.public.publication_status = "unpublished"
    elif reason == "unapproved":
        c.public.review_status = "rejected"
    elif reason == "revision":
        c.public.editorial_revision += 1
    else:
        c.authority.return_value = False
    before = deepcopy(c.owner.payload)
    order = []

    def authority(session, identifier, *, lock=False):
        if lock:
            order.append("source")
        return reason != "source"

    scalar_rows = iter([None if reason == "missing" else c.public, c.owner])

    def scalar(statement):
        order.append("article" if len(order) == 1 else "owner")
        return next(scalar_rows)

    c.authority.side_effect = authority
    c.writer.scalar.side_effect = scalar
    assert recovery.resume_refresh(c.factory, c.owner.id) == "superseded"
    assert c.owner.status == "superseded" and c.owner.version == 3 and c.owner.completed_at == NOW
    assert c.owner.payload == before and order == ["source", "article", "owner"]
    assert c.events == ["metadata", "metadata_closed", "write", "write_closed"]
    c.prepare.assert_not_called()
    c.writer.add.assert_not_called()


@pytest.mark.parametrize("changed", ["owner_missing", "terminal", "version", "authority_restored"])
def test_recovery_does_not_apply_a_stale_withdrawal_observation_after_another_transaction(
    continuation, changed
):
    c = continuation
    # An earlier metadata snapshot saw a withdrawn Article. The locked re-read
    # below supplies current rows, including a possible restored publication.
    c.rows[Article] = Article(
        id=c.public.id,
        publication_status="unpublished",
        review_status="approved",
        editorial_revision=3,
    )
    if changed == "owner_missing":
        c.writer.scalar.side_effect = [c.public, None]
    elif changed == "terminal":
        locked = ArticleRefresh(
            id=c.owner.id, article_id=c.public.id, status="cancelled", version=2, base_revision=3
        )
        c.writer.scalar.side_effect = [c.public, locked]
    elif changed == "version":
        locked = ArticleRefresh(
            id=c.owner.id, article_id=c.public.id, status="active", version=3, base_revision=3
        )
        c.writer.scalar.side_effect = [c.public, locked]
    assert recovery.resume_refresh(c.factory, c.owner.id) == "pending"
    assert c.owner.status == "active" and c.owner.version == 2
    c.writer.add.assert_not_called()
    c.prepare.assert_not_called()


@pytest.mark.parametrize("status", ["promoted", "pending", "review", "superseded"])
def test_image_owner_uses_existing_image_promotion_without_body_or_catalog_preparation(
    continuation, status
):
    c = continuation
    c.owner.target = "images"
    c.promote.return_value = status
    assert recovery.resume_refresh(c.factory, c.owner.id) == status
    c.promote.assert_called_once_with(c.factory, c.owner.id)
    c.prepare.assert_not_called()
    c.factory.begin.assert_not_called()


@pytest.mark.parametrize("target", ["enrichment", "analysis"])
@pytest.mark.parametrize("primary", ["missing", "foreign", "failed", "queued"])
def test_nonimage_recovery_requires_its_exact_completed_target_without_retrying_failures(
    continuation, target, primary
):
    c = continuation
    c.owner.target = target
    # Metadata inspection can precede a new selection; preparation supplies the
    # current selected artifact rather than replaying the earlier job snapshot.
    current = ArticleEnrichmentJob if target == "enrichment" else ArticleAnalysisJob
    c.inputs.facts[target] = (
        None
        if primary == "missing"
        else current(
            id=uuid.uuid4(),
            article_id=c.public.id,
            refresh_id=uuid.uuid4() if primary == "foreign" else c.owner.id,
            status="succeeded" if primary == "foreign" else primary,
        )
    )
    assert recovery.resume_refresh(c.factory, c.owner.id) == "pending"
    c.prepare.assert_called_once_with(c.reader, c.owner.id)
    c.analysis_admission.assert_not_called()
    c.image_admission.assert_not_called()
    c.promote.assert_not_called()


@pytest.mark.parametrize("status", ["failed", "stale", "missing"])
def test_enrichment_blockers_do_not_trigger_more_private_analysis_or_images(continuation, status):
    c = continuation
    c.readiness.checks["enrichment"].status = status
    assert recovery.resume_refresh(c.factory, c.owner.id) == "blocked"
    c.factory.begin.assert_not_called()
    c.analysis_admission.assert_not_called()
    c.image_admission.assert_not_called()


@pytest.mark.parametrize(
    "reason",
    [
        "failed_private",
        "insufficient_private",
        "paywalled_content",
        "insufficient_source_text",
        "content_date_deferred",
    ],
)
def test_recovery_never_turns_terminal_private_or_ineligible_evidence_into_automatic_retry(
    continuation, reason
):
    c = continuation
    c.owner.target = "enrichment"
    c.readiness.checks["analysis"].status = "stale"
    if reason in {"failed_private", "insufficient_private"}:
        c.inputs.facts["analysis"] = ArticleAnalysisJob(
            id=uuid.uuid4(),
            article_id=c.public.id,
            refresh_id=c.owner.id,
            status="failed" if reason == "failed_private" else "succeeded",
            outcome="insufficient_evidence" if reason == "insufficient_private" else None,
            input_hash=c.inputs.input_hash,
        )
        if reason == "insufficient_private":
            c.readiness.checks["analysis"].status = "unavailable"
    else:
        c.readiness.blockers = [reason]
    assert recovery.resume_refresh(c.factory, c.owner.id) == "blocked"
    c.factory.begin.assert_not_called()
    c.analysis_admission.assert_not_called()


@pytest.mark.parametrize("outcome", ["failed", "insufficient_evidence"])
@pytest.mark.parametrize("changed", ["none", "input", "catalog"])
def test_old_private_failure_blocks_only_the_same_current_input_and_catalog(
    continuation, outcome, changed
):
    c = continuation
    c.owner.target = "enrichment"
    c.inputs.facts["analysis"] = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=c.public.id,
        refresh_id=c.owner.id,
        status="failed" if outcome == "failed" else "succeeded",
        outcome="insufficient_evidence" if outcome == "insufficient_evidence" else None,
        input_hash=c.inputs.input_hash,
    )
    c.readiness.checks["analysis"].status = (
        "unavailable" if outcome == "insufficient_evidence" and changed == "none" else "stale"
    )
    if changed == "input":
        c.inputs.input_hash = "b" * 64
    elif changed == "catalog":
        c.catalog_current.return_value = False
    result = recovery.resume_refresh(c.factory, c.owner.id)
    if changed == "none":
        assert result == "blocked"
        c.analysis_admission.assert_not_called()
        c.factory.begin.assert_not_called()
    else:
        assert result == "analysis_queued"
        c.analysis_admission.assert_called_once_with(c.writer, c.owner.id, prepared=c.inputs)
    assert c.public.publication_status == "published" and c.owner.status == "active"


@pytest.mark.parametrize("previous", ["missing", "ordinary_failed", "ordinary_stale"])
def test_enrichment_continuation_admits_bound_analysis_only_after_read_preparation_closes(
    continuation, previous
):
    c = continuation
    c.owner.target = "enrichment"
    c.readiness.checks["analysis"].status = "stale"
    c.inputs.facts["analysis"] = (
        None
        if previous == "missing"
        else ArticleAnalysisJob(
            id=uuid.uuid4(),
            article_id=c.public.id,
            refresh_id=None,
            status="failed" if previous == "ordinary_failed" else "succeeded",
            outcome="applied",
        )
    )

    def admit(session, identifier, *, prepared):
        assert c.events == ["metadata", "metadata_closed", "prepare", "prepare_closed", "write"]
        assert session is c.writer and identifier == c.owner.id and prepared is c.inputs

    c.analysis_admission.side_effect = admit
    assert recovery.resume_refresh(c.factory, c.owner.id) == "analysis_queued"
    c.analysis_admission.assert_called_once()
    assert c.events[-1] == "write_closed"
    c.image_admission.assert_not_called()
    c.promote.assert_not_called()


@pytest.mark.parametrize("status", ["missing", "stale"])
@pytest.mark.parametrize("accepted", [False, True])
def test_image_continuation_keeps_private_owner_and_reports_admission_conflicts(
    continuation, status, accepted
):
    c = continuation
    c.readiness.checks["images"].status = status
    c.image_admission.return_value = object() if accepted else None

    def admit(session, identifier, *, automatic, refresh_id):
        assert c.events == ["metadata", "metadata_closed", "prepare", "prepare_closed", "write"]
        assert session is c.writer and identifier == c.public.id
        assert automatic is True and refresh_id == c.owner.id
        return c.image_admission.return_value

    c.image_admission.side_effect = admit
    assert recovery.resume_refresh(c.factory, c.owner.id) == (
        "images_queued" if accepted else "blocked"
    )
    c.image_admission.assert_called_once()
    c.promote.assert_not_called()


def test_failed_image_continuation_requires_explicit_retry(continuation):
    c = continuation
    c.readiness.checks["images"].status = "failed"
    assert recovery.resume_refresh(c.factory, c.owner.id) == "blocked"
    c.factory.begin.assert_not_called()
    c.image_admission.assert_not_called()


@pytest.mark.parametrize("images", ["complete", "unavailable"])
def test_complete_nonimage_owner_reaches_guarded_promotion_without_new_jobs(continuation, images):
    c = continuation
    c.readiness.checks["images"].status = images
    assert recovery.resume_refresh(c.factory, c.owner.id) == "promoted"
    assert c.events == ["metadata", "metadata_closed", "prepare", "prepare_closed"]
    c.promote.assert_called_once_with(c.factory, c.owner.id)
    c.analysis_admission.assert_not_called()
    c.image_admission.assert_not_called()


@pytest.mark.parametrize("limit", [-1, 0, 501])
def test_invalid_reconciliation_limit_never_claims_any_owner(limit):
    factory = Mock()
    with pytest.raises(ValueError, match="between 1 and 500"):
        recovery.reconcile_refreshes(factory, limit, NOW)
    factory.begin.assert_not_called()


def test_no_due_owner_does_not_resume_or_reschedule_other_work(monkeypatch):
    session = Mock(spec=Session)
    session.scalars.return_value.all.return_value = []
    factory = Mock()
    factory.begin.return_value = nullcontext(session)
    resume = Mock()
    monkeypatch.setattr(recovery, "resume_refresh", resume)
    assert recovery.reconcile_refreshes(factory, 20, NOW) == 0
    resume.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("limit", [1, 20, 500])
@pytest.mark.parametrize("outcome", ["success", "conflict", "unexpected"])
def test_reconciliation_commits_bounded_due_claims_before_resuming_and_keeps_other_owners_running(
    monkeypatch, caplog, limit, outcome
):
    owners = [
        ArticleRefresh(
            id=uuid.uuid4(),
            article_id=uuid.uuid4(),
            status="active" if index == 0 else "review",
            next_check_at=NOW - timedelta(seconds=1),
            payload={"private": str(index)},
        )
        for index in range(2 if limit > 1 else 1)
    ]
    original_payloads = [deepcopy(owner.payload) for owner in owners]
    session, factory = Mock(spec=Session), Mock()
    session.scalars.return_value.all.return_value = owners
    phases = []

    @contextmanager
    def claim():
        phases.append("claim")
        try:
            yield session
        finally:
            phases.append("committed")

    factory.begin.return_value = claim()
    continued = []

    def resume(actual_factory, identifier):
        assert actual_factory is factory and phases == ["claim", "committed"]
        assert all(owner.next_check_at == NOW + timedelta(minutes=5) for owner in owners)
        continued.append(identifier)
        if len(continued) == 1 and outcome == "conflict":
            raise OperationConflict("A new candidate version won after this claim")
        if len(continued) == 1 and outcome == "unexpected":
            raise RuntimeError("Controlled recovery failure")

    monkeypatch.setattr(recovery, "resume_refresh", resume)
    monkeypatch.setattr(recovery, "utcnow", lambda: NOW)
    with caplog.at_level(logging.ERROR, logger=recovery.__name__):
        count = recovery.reconcile_refreshes(factory, limit)
    assert count == len(owners) and continued == [owner.id for owner in owners]
    assert [owner.payload for owner in owners] == original_payloads
    assert [owner.status for owner in owners] == ["active", "review"][: len(owners)]
    query = session.scalars.call_args.args[0]
    rendered = str(query.compile(dialect=dialect(), compile_kwargs={"literal_binds": True}))
    assert f"LIMIT {min(limit, 20)}" in rendered and "SKIP LOCKED" in rendered
    assert "ORDER BY article_refreshes.next_check_at, article_refreshes.id" in rendered
    assert not re.search(r"article_refreshes\.payload(?:[,\s])", rendered)
    errors = [
        record
        for record in caplog.records
        if record.message == "article_maintenance_reconciliation_failed"
    ]
    assert len(errors) == (1 if outcome == "unexpected" else 0)
    if errors:
        assert errors[0].refresh_id == str(owners[0].id)
