"""Operator recovery uses current evidence and never grants editorial authority."""

from copy import deepcopy
from datetime import timedelta

import pytest
from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import fail_or_retry, finish_job
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import (
    ArticleImageJob,
    CandidateFeed,
    IngestionJob,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceEnrichmentJob,
    SourceReview,
    utcnow,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_lifecycle import (
    advance_source_processing,
    reconcile_source_processing,
    retry_source_processing,
    source_readiness,
    source_readiness_batch,
)
from devfeed_core.source_logos import request_source_logo
from devfeed_core.source_relevance import VERSION
from sqlalchemy import func, select

from tests.sources.test_source_lifecycle import SAMPLE, seed_source

pytestmark = pytest.mark.integration


def standalone_facts(session, candidate):
    return source_readiness_batch(session, [], [candidate])[f"candidate:{candidate.id}"]


def test_discovery_advance_and_retry_keep_history_without_attaching_an_unverified_source(database):
    with database.begin() as session:
        candidate = SourceCandidate(name="Publisher", identity_url="https://publisher.example/")
        session.add(candidate)
        session.flush()
        before = standalone_facts(session, candidate)
        queued = advance_source_processing(session, "candidate", candidate.id, before.snapshot)
        old = queued.jobs["discovery"]
        assert old.status == "queued" and old.result["background"] is True
        assert queued.readiness.stage == "discovery" and queued.readiness.next_action is None
        assert candidate.source_id is None
        with pytest.raises(OperationConflict, match="changed"):
            advance_source_processing(session, "candidate", candidate.id, before.snapshot)
        fail_or_retry(old, "No working feed found", utcnow(), retryable=False)
        session.flush()
        failed = standalone_facts(session, candidate)
        assert failed.readiness.state == "blocked"
        assert failed.readiness.retry_targets == ["discovery"]
        retried = retry_source_processing(
            session, "candidate", candidate.id, failed.snapshot, "discovery", old.id
        )
        replacement = retried.jobs["discovery"]
        assert replacement.id != old.id and replacement.status == "queued"
        assert replacement.result["background"] is True
        assert old.status == "failed" and old.error == "No working feed found"
        assert candidate.status == "pending" and candidate.source_id is None
        assert session.scalar(select(func.count()).select_from(SourceDiscoveryJob)) == 2
        assert session.scalar(select(func.count()).select_from(Source)) == 0


def test_selected_discovery_feed_advances_to_pending_source_profile_without_approval(database):
    with database.begin() as session:
        candidate = SourceCandidate(
            name="Publisher", identity_url="https://publisher.example/", status="ready"
        )
        session.add(candidate)
        session.flush()
        feed = CandidateFeed(candidate_id=candidate.id, url="https://publisher.example/feed")
        session.add(feed)
        session.flush()
        candidate.selected_feed_id = feed.id
        facts = standalone_facts(session, candidate)
        assert facts.readiness.next_action == "profile"
        attached = advance_source_processing(session, "candidate", candidate.id, facts.snapshot)
        assert attached.key == f"source:{candidate.id}"
        assert attached.source.id == candidate.source_id
        assert attached.source.feed_url == feed.url
        assert attached.source.approval_status == "pending" and not attached.source.enabled
        profile = attached.jobs["profile"]
        assert profile.operation == "profile" and profile.status == "queued"
        assert profile.result["input"]["feed_url"] == feed.url
        assert attached.readiness.stage == "profile" and attached.readiness.state == "waiting"
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        with pytest.raises(OperationConflict, match="linked to a source"):
            advance_source_processing(session, "candidate", candidate.id, facts.snapshot)


@pytest.mark.parametrize("rejected", [False, True])
def test_website_without_candidate_starts_discovery_and_reopen_retains_manual_review(
    database, rejected
):
    with database.begin() as session:
        source = Source(
            name="Publisher website",
            website_url="https://publisher.example/",
            source_type="publisher",
            approval_status="rejected" if rejected else "pending",
            enabled=False,
        )
        session.add(source)
        session.flush()
        before = reconcile_source_processing(session, source)
        assert before.candidate is None
        assert before.readiness.next_action == ("reopen" if rejected else "discovery")
        advanced = advance_source_processing(
            session, "source", source.id, before.snapshot, actor={"subject": "editor"}
        )
        assert advanced.candidate.source_id == source.id
        assert advanced.candidate.identity_url == source.website_url
        assert advanced.jobs["discovery"].result["background"] is True
        assert source.approval_status == "pending" and not source.enabled
        assert advanced.readiness.stage == "discovery"
        if rejected:
            assert source.processing_request["manual_review"] is True
            assert source.processing_request["actor"] == {"subject": "editor"}
        else:
            assert source.processing_request == {}
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0


def test_explicit_assessment_advance_binds_current_profile_and_keeps_review_pending(
    database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    with database.begin() as session:
        source = seed_source(session)
        facts = source_readiness(session, source)
        assert facts.readiness.next_action == "assessment"
        advanced = advance_source_processing(session, "source", source.id, facts.snapshot)
        job = advanced.jobs["assessment"]
        assert job.operation == "relevance" and job.status == "queued"
        assert job.result["profile_job_id"] == str(facts.jobs["profile"].id)
        assert job.result["policy_version"] == VERSION and job.result["sample"] == SAMPLE
        assert advanced.readiness.stage == "assessment" and advanced.readiness.state == "waiting"
        assert source.approval_status == "pending" and not source.enabled
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0


@pytest.mark.parametrize("automatic", [False, True])
def test_changed_logo_replaces_failed_old_input_through_explicit_or_automatic_processing(
    database, monkeypatch, automatic
):
    monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
    with database.begin() as session:
        source = seed_source(session, logo="https://publisher.example/old-logo.png")
        old = session.scalar(select(ArticleImageJob).where(ArticleImageJob.source_id == source.id))
        fail_or_retry(old, "Old logo is unavailable", utcnow(), retryable=False)
        # Metadata can change while storage is disabled; enabling it later must
        # recover the current logo rather than retry the obsolete URL.
        monkeypatch.setattr(get_settings(), "image_storage_enabled", False)
        source.logo_url = "https://publisher.example/new-logo.png"
        session.flush()
        monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
        facts = source_readiness(session, source)
        assert facts.readiness.next_action == "branding"
        assert facts.readiness.checks["branding"].status == "stale"
        advanced = (
            reconcile_source_processing(session, source)
            if automatic
            else advance_source_processing(session, "source", source.id, facts.snapshot)
        )
        replacement = advanced.jobs["branding"]
        assert replacement.id != old.id and replacement.status == "queued"
        assert replacement.image_url == source.logo_url
        assert old.status == "failed" and old.error == "Old logo is unavailable"
        assert not advanced.readiness.can_approve
        assert source.approval_status == "pending" and not source.enabled
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0


def test_failed_ingestion_retry_keeps_approval_and_adaptive_failure_cooldown(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", True)
    with database.begin() as session:
        source = seed_source(session, approved=True)
        old = request_ingestion(session, source, automatic=True)
        old.attempts = 3
        fail_or_retry(old, "Publisher unavailable", utcnow(), retryable=False)
        source.polling_mode = "adaptive"
        source.consecutive_failures = 3
        cooldown = source.next_fetch_at = utcnow() + timedelta(hours=2)
        session.flush()
        facts = source_readiness(session, source)
        assert facts.readiness.stage == "ingestion" and facts.readiness.state == "blocked"
        retried = retry_source_processing(
            session, "source", source.id, facts.snapshot, "ingestion", old.id
        )
        job = retried.jobs["ingestion"]
        assert job.id != old.id and job.status == "queued" and job.available_at == cooldown
        assert old.status == "failed" and old.attempts == 3 and old.error == "Publisher unavailable"
        assert source.enabled and source.approval_status == "approved"
        assert source.processing_request == {} and source.consecutive_failures == 3
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0


def test_failed_branding_replacement_blocks_admission_despite_previously_usable_logo(
    database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
    monkeypatch.setattr(get_settings(), "image_public_url", "https://images.example")
    with database.begin() as session:
        source = seed_source(session, logo="https://publisher.example/logo.png")
        source.managed_logo = {
            "source_url": source.logo_url,
            "version": "v1",
            "variants": [{"key": f"logo-{width}", "width": width} for width in (16, 32, 64, 96)],
        }
        original = session.scalar(
            select(ArticleImageJob).where(ArticleImageJob.source_id == source.id)
        )
        finish_job(original, "found", utcnow())
        session.flush()
        assert source_readiness(session, source).readiness.can_approve
        previous_asset = deepcopy(source.managed_logo)
        failed = request_source_logo(session, source.id, refresh=True)
        assert failed.id != original.id and failed.operation == "source-logo-refresh"
        fail_or_retry(failed, "Replacement logo processing failed", utcnow(), retryable=False)
        session.flush()
        for _ in range(2):
            facts = reconcile_source_processing(session, source)
            assert facts.readiness.stage == "branding" and facts.readiness.state == "blocked"
            assert facts.readiness.checks["branding"].status == "failed"
            assert facts.readiness.next_action is None and not facts.readiness.can_approve
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 2
        retried = retry_source_processing(
            session,
            "source",
            source.id,
            facts.snapshot,
            "branding",
            failed.id,
            actor={"subject": "operator"},
        )
        replacement = retried.jobs["branding"]
        assert replacement.id != failed.id and replacement.operation == "source-logo-refresh"
        assert replacement.storage["source_pipeline_request"]["actor"] == {"subject": "operator"}
        assert source.approval_status == "pending" and not source.enabled
        assert source.managed_logo == previous_asset and original.status == "succeeded"
        assert failed.status == "failed" and failed.error == "Replacement logo processing failed"


def test_processing_snapshot_rejects_changed_automation_policy_before_creating_work(
    database, monkeypatch
):
    with database.begin() as session:
        source = seed_source(session, profile=False)
        facts = source_readiness(session, source)
        monkeypatch.setattr(get_settings(), "full_automation", True)
        with pytest.raises(OperationConflict, match="changed"):
            advance_source_processing(session, "source", source.id, facts.snapshot)
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0
        current = source_readiness(session, source)
        advanced = advance_source_processing(session, "source", source.id, current.snapshot)
        assert advanced.jobs["profile"].status == "queued"
        assert source.approval_status == "pending" and not source.enabled


@pytest.mark.parametrize("kind", ["source", "candidate"])
def test_deleted_lifecycle_item_cannot_create_work_from_its_old_snapshot(database, kind):
    with database.begin() as session:
        if kind == "source":
            row = seed_source(session, profile=False)
            facts = source_readiness(session, row)
        else:
            row = SourceCandidate(name="Publisher", identity_url="https://publisher.example/")
            session.add(row)
            session.flush()
            facts = standalone_facts(session, row)
        identifier = row.id
        session.delete(row)
        session.flush()
        with pytest.raises(RecordNotFound, match="not found"):
            advance_source_processing(session, kind, identifier, facts.snapshot)
        assert session.scalar(select(func.count()).select_from(SourceDiscoveryJob)) == 0
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0
