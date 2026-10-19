"""First admission and explicit source recovery share durable processing facts."""

import hashlib
import importlib
import json
import uuid
from datetime import timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_aggregator import source_tasks, tasks
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.inference_errors import AnalysisError
from devfeed_core.job_retries import retry_failed_job
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    ArticleOrigin,
    CandidateFeed,
    IngestionJob,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceEnrichmentJob,
    SourceReview,
    utcnow,
)
from devfeed_core.schemas import SourceDecision, SourcePatch
from devfeed_core.services import OperationConflict, review_source, update_source
from devfeed_core.source_enrichment import request_enrichment, request_relevance, source_identity
from devfeed_core.source_lifecycle import (
    advance_source_processing,
    reconcile_source_processing,
    retry_source_processing,
    source_readiness,
)
from devfeed_core.source_relevance import VERSION, schedule_pending_reviews
from sqlalchemy import func, select, text
from test_logo_integration import enabled as enabled
from test_logo_integration import process_logo

pytestmark = pytest.mark.integration
SAMPLE = [
    {"index": index, "title": f"Python task tracing and runtime inspection {index}", "summary": ""}
    for index in range(3)
]


def seed_source(session, *, approved=False, profile=True, logo=None):
    source = Source(
        name="Python engineering",
        feed_url="https://publisher.example/feed",
        website_url="https://publisher.example/",
        source_type="publisher",
        approval_status="approved" if approved else "pending",
        enabled=approved,
        logo_url=logo,
        last_success_at=utcnow() - timedelta(days=1) if approved else None,
    )
    session.add(source)
    session.flush()
    if profile:
        session.add(
            SourceEnrichmentJob(
                source_id=source.id,
                operation="profile",
                status="succeeded",
                finished_at=utcnow(),
                result={
                    "input": source_identity(source),
                    "sample": SAMPLE,
                    "sample_hash": hashlib.sha256(json.dumps(SAMPLE).encode()).hexdigest(),
                },
            )
        )
        source.metadata_enriched_at = utcnow()
        session.flush()
    return source


def assessment(source, *, approved=True, rejected=False):
    return {
        "version": VERSION,
        "feed_url": source.feed_url,
        "source_type": source.source_type,
        "sample": SAMPLE,
        "relevance": "relevant" if approved else "uncertain",
        "reason": "The sampled articles discuss Python engineering and runtime tooling.",
        "approval_supported": approved,
        "rejection_supported": rejected,
    }


def complete_branding(session, source):
    job = session.scalar(select(ArticleImageJob).where(ArticleImageJob.source_id == source.id))
    source.managed_logo = {
        "source_url": source.logo_url,
        "version": "v1",
        "variants": [{"key": f"logo-{width}", "width": width} for width in (16, 32, 64, 96)],
    }
    job.status, job.outcome, job.finished_at = "succeeded", "found", utcnow()
    session.flush()
    reconcile_source_processing(session, source)


def test_website_profile_completion_retains_frozen_assessment_sample(monkeypatch):
    body = (
        b"<rss><channel><title>Python engineering</title><link>https://publisher.example/</link>"
        b"<item><title>Python task tracing and runtime inspection</title>"
        b"<link>https://publisher.example/tracing</link></item></channel></rss>"
    )
    monkeypatch.setattr(source_tasks, "fetch_feed", lambda url: FetchResult(200, body, url))
    monkeypatch.setattr(
        source_tasks,
        "complete_website_profile",
        lambda *a, **kw: {"description": "A completed website profile"},
    )
    values, error = source_tasks.lookup_profile(
        "https://publisher.example/feed",
        "publisher",
        dict.fromkeys(["description", "website_url", "logo_url", "image_url", "language"]),
    )
    assert error is None and values["description"] == "A completed website profile"
    assert values["_sample"][0]["title"] == "Python task tracing and runtime inspection"


def test_profile_error_never_spends_ai_or_approves_partial_metadata(database, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    with database.begin() as session:
        source = seed_source(session, profile=False)
        identifier, job_id = source.id, request_enrichment(session, source.id).id
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        source_tasks,
        "lookup_profile",
        lambda *_: (
            {"description": "A retained partial profile", "_sample": SAMPLE},
            FeedError("unavailable", reason="http_error", status=503, retryable=False),
        ),
    )
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: pytest.fail("No AI"))
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "pending" and not source.enabled
        assert source.description == "A retained partial profile" and source.metadata_error
        assert session.get(SourceEnrichmentJob, job_id).status == "failed"
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 1
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0


def test_profile_and_assessment_have_independent_retries_and_one_initial_ingestion(
    database, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    with database.begin() as session:
        source = seed_source(session, profile=False)
        identifier, first_id = source.id, request_enrichment(session, source.id).id
        output = assessment(source)
    lookups, assessments = [], []
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        source_tasks,
        "lookup_profile",
        lambda *_: (lookups.append("profile") or {"_sample": SAMPLE}, None),
    )

    def fail(*args, **kwargs):
        assessments.append(kwargs["sample"])
        raise AnalysisError("codex_unavailable")

    monkeypatch.setattr(source_tasks, "assess_source", fail)
    source_tasks.enrich_source(str(first_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        ai = session.scalar(
            select(SourceEnrichmentJob).where(SourceEnrichmentJob.operation == "relevance")
        )
        assert source.metadata_enriched_at and not source.metadata_error
        assert not source.enabled and lookups == ["profile"] and not assessments
        ai.attempts, failed_id = 5, ai.id
    source_tasks.enrich_source(str(failed_id))
    with database.begin() as session:
        previous = session.get(SourceEnrichmentJob, failed_id)
        assert previous.status == "failed"
        successor = retry_failed_job(
            session, previous, "source-enrichment", {"subject": "operator"}
        )
        retry_id = successor.id
        assert successor.operation == "relevance" and successor.result["sample"] == SAMPLE
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: output)
    source_tasks.enrich_source(str(retry_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "approved" and source.enabled
        assert lookups == ["profile"] and assessments == [SAMPLE]
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 3


@pytest.mark.parametrize("branding_first", [True, False])
def test_automatic_admission_waits_for_all_successful_work_in_either_completion_order(
    database, monkeypatch, branding_first
):
    settings = get_settings()
    for name, value in (
        ("ai_enabled", True),
        ("full_automation", True),
        ("image_storage_enabled", True),
        ("image_public_url", "https://images.example"),
    ):
        monkeypatch.setattr(settings, name, value)
    with database.begin() as session:
        source = seed_source(session, logo="https://publisher.example/logo.png")
        identifier, output = source.id, assessment(source)
        ai_id = request_relevance(session, source.id).id
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: output)
    if branding_first:
        with database.begin() as session:
            source = session.get(Source, identifier)
            complete_branding(session, source)
            assert source.approval_status == "pending"
    source_tasks.enrich_source(str(ai_id))
    if not branding_first:
        with database.begin() as session:
            source = session.get(Source, identifier)
            assert source.approval_status == "pending" and not source.enabled
            assert source_readiness(session, source).readiness.stage == "branding"
            complete_branding(session, source)
    assert schedule_pending_reviews(database) == 0
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "approved" and source.enabled
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 2


def test_queued_assessment_obeys_ai_kill_switch_without_consuming_attempt(database, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    with database.begin() as session:
        source = seed_source(session)
        job_id = request_relevance(session, source.id).id
    monkeypatch.setattr(settings, "ai_enabled", False)
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: pytest.fail("AI disabled"))
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        job = session.get(SourceEnrichmentJob, job_id)
        assert job.status == "queued" and job.attempts == 0 and job.lease_token is None


@pytest.mark.parametrize("enabled", [False, True])
def test_ordinary_approved_refresh_never_backfills_or_changes_ingestion_intent(
    database, monkeypatch, enabled
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    with database.begin() as session:
        source = seed_source(session, approved=True)
        source.enabled = enabled
        refresh = request_enrichment(session, source.id)
        refresh.status, refresh.error = "failed", "Refresh exhausted"
        source.website_url = "https://updated.example/"
        session.flush()
        for _ in range(2):
            facts = reconcile_source_processing(session, source)
            assert facts.readiness.stage == ("active" if enabled else "paused")
        assert source.approval_status == "approved" and source.enabled is enabled
        assert source.processing_request == {}
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 2
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0


@pytest.mark.parametrize("changed", ["feed_url", "source_type", "website_url"])
def test_unbound_legacy_profile_cannot_admit_edited_pending_source(database, changed):
    with database.begin() as session:
        source = seed_source(session, profile=False)
        source.metadata_enriched_at = utcnow()
        source.relevance_assessment = assessment(source)
        session.add(
            SourceEnrichmentJob(source_id=source.id, operation="legacy", status="succeeded")
        )
        setattr(
            source,
            changed,
            "aggregator" if changed == "source_type" else "https://changed.example/",
        )
        with pytest.raises(OperationConflict, match="profile_"):
            review_source(session, source.id, SourceDecision(decision="approved"))
        assert source.approval_status == "pending" and not source.enabled


@pytest.mark.parametrize("when", ["before_claim", "during_lookup"])
def test_explicit_pause_survives_stale_job_replacement_until_manual_resume(
    database, monkeypatch, when
):
    with database.begin() as session:
        source = seed_source(session, approved=True)
        identifier, success_at = source.id, source.last_success_at
        facts = source_readiness(session, source)
        updated = retry_source_processing(
            session, "source", source.id, facts.snapshot, "profile", facts.jobs["profile"].id
        )
        job_id = updated.jobs["profile"].id
        assert not source.enabled and source.processing_request

    def lookup(*args):
        with database.begin() as session:
            session.get(Source, identifier).website_url = "https://changed.example/"
        return {"description": "Obsolete response", "_sample": SAMPLE}, None

    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    if when == "before_claim":
        with database.begin() as session:
            session.get(Source, identifier).website_url = "https://changed.example/"
    source_tasks.enrich_source(str(job_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        # A stale owner automatically queues the current input while preserving
        # the operator's pause. Resume stays explicit after successful work.
        current = source_readiness(session, source)
        assert source.processing_request and not source.enabled
        assert current.readiness.stage == "profile"
        replacement_id = current.jobs["profile"].id
        assert replacement_id != job_id and current.jobs["profile"].status == "queued"
        assert session.get(SourceEnrichmentJob, job_id).status == "failed"
        assert source.description is None
        with pytest.raises(OperationConflict, match="profile_"):
            update_source(session, identifier, SourcePatch(enabled=True))
        assert not source.enabled
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *_: ({"_sample": SAMPLE}, None))
    source_tasks.enrich_source(str(replacement_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        facts = source_readiness(session, source)
        assert facts.readiness.stage == "paused" and facts.readiness.next_action == "resume"
        assert (
            not source.enabled
            and source.processing_request
            and source.last_success_at == success_at
        )
        resumed = advance_source_processing(session, "source", identifier, facts.snapshot)
        assert source.enabled and source.processing_request == {}
        assert resumed.readiness.stage == "ingestion"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("when", ["before_claim", "during_lookup"])
def test_stale_ordinary_approved_refresh_does_not_queue_unrequested_successors(
    database, monkeypatch, enabled, when
):
    with database.begin() as session:
        source = seed_source(session, approved=True)
        source.enabled = enabled
        identifier, old_id = source.id, request_enrichment(session, source.id).id
        if when == "before_claim":
            source.website_url = "https://changed.example/"

    def lookup(*args):
        with database.begin() as session:
            session.get(Source, identifier).website_url = "https://changed.example/"
        return {"description": "Obsolete metadata", "_sample": SAMPLE}, None

    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    source_tasks.enrich_source(str(old_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "approved" and source.enabled is enabled
        assert source.processing_request == {} and source.description is None
        assert session.get(SourceEnrichmentJob, old_id).status == "failed"
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 2
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0


def test_upstream_retry_fences_inflight_ingestion_without_feed_failure_backoff(
    database, monkeypatch
):
    from devfeed_core.jobs import request_ingestion

    with database.begin() as session:
        source = seed_source(session, approved=True)
        identifier, success_at = source.id, source.last_success_at
        job_id = request_ingestion(session, source).id

    def fetch(url, *args, **kwargs):
        with database.begin() as session:
            source = session.get(Source, identifier)
            facts = source_readiness(session, source)
            retry_source_processing(
                session, "source", identifier, facts.snapshot, "profile", facts.jobs["profile"].id
            )
        return FetchResult(
            200,
            b"<rss><channel><title>Python</title><item><title>Python tracing</title><link>https://publisher.example/article</link></item></channel></rss>",
            url,
        )

    monkeypatch.setattr(tasks, "session_factory", lambda: database)
    monkeypatch.setattr(tasks, "fetch_feed", fetch)
    tasks.ingest(str(job_id))
    with database() as session:
        source, job = session.get(Source, identifier), session.get(IngestionJob, job_id)
        assert not source.enabled and source.approval_status == "approved"
        assert job.status == "failed" and job.lease_token is None
        assert source.consecutive_failures == 0 and source.last_success_at == success_at
        assert session.scalar(select(func.count()).select_from(Article)) == 0


def test_requested_assessment_survives_profile_replacement_without_automatic_resume(
    database, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    with database.begin() as session:
        source = seed_source(session, approved=True)
        identifier, output = source.id, assessment(source, approved=False)
        facts = source_readiness(session, source)
        requested = retry_source_processing(
            session, "source", identifier, facts.snapshot, "assessment", None
        )
        first_id = requested.jobs["assessment"].id
        assert "assessment" in source.processing_request["required"]
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: output)
    source_tasks.enrich_source(str(first_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        facts = source_readiness(session, source)
        assert facts.readiness.stage == "paused" and not source.enabled
        replacement = retry_source_processing(
            session, "source", identifier, facts.snapshot, "profile", facts.jobs["profile"].id
        )
        profile_id = replacement.jobs["profile"].id
        assert "assessment" in source.processing_request["required"]
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *_: ({"_sample": SAMPLE}, None))
    source_tasks.enrich_source(str(profile_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        facts = source_readiness(session, source)
        second_id = facts.jobs["assessment"].id
        assert second_id != first_id and facts.readiness.stage == "assessment"
        assert not source.enabled
        with pytest.raises(OperationConflict, match="assessment_"):
            update_source(session, identifier, SourcePatch(enabled=True))
    source_tasks.enrich_source(str(second_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        facts = source_readiness(session, source)
        assert facts.readiness.stage == "paused" and source.processing_request
        assert not source.enabled and source.approval_status == "approved"
        # Idempotent editorial approval does not silently undo the processing pause.
        review_source(session, identifier, SourceDecision(decision="approved"))
        assert not source.enabled and source.processing_request
        advance_source_processing(session, "source", identifier, facts.snapshot)
        assert source.enabled and source.processing_request == {}


@pytest.mark.parametrize("active", [False, True])
def test_disabled_storage_allows_settled_branding_fallback_but_keeps_active_work_blocking(
    database, monkeypatch, active
):
    settings = get_settings()
    monkeypatch.setattr(settings, "image_storage_enabled", True)
    with database.begin() as session:
        source = seed_source(session, logo="https://publisher.example/logo.png")
        job = session.scalar(select(ArticleImageJob).where(ArticleImageJob.source_id == source.id))
        job.status = "running" if active else "failed"
        job.error = "Original branding attempt failed" if not active else None
        session.flush()
        monkeypatch.setattr(settings, "image_storage_enabled", False)
        facts = source_readiness(session, source)
        assert facts.readiness.can_approve is not active
        assert facts.readiness.stage == ("branding" if active else "review")
        if active:
            with pytest.raises(OperationConflict, match="branding_"):
                review_source(session, source.id, SourceDecision(decision="approved"))
        else:
            review_source(session, source.id, SourceDecision(decision="approved"))
            assert source.enabled


def test_manual_new_approval_clears_old_pause_intent_and_may_override_failed_ai(database):
    with database.begin() as session:
        source = seed_source(session, approved=True)
        source.enabled = False
        source.processing_request = {"required": ["profile", "assessment", "branding"]}
        session.add(
            SourceEnrichmentJob(source_id=source.id, operation="relevance", status="failed")
        )
        review_source(
            session, source.id, SourceDecision(decision="rejected", note="Wrong source policy")
        )
        rejected = source_readiness(session, source).readiness
        assert rejected.stage == "rejected" and rejected.can_approve
        assert rejected.next_action is None and rejected.retry_targets == []
        assert source.processing_request and not source.enabled
        review_source(session, source.id, SourceDecision(decision="approved"))
        assert source.enabled and source.processing_request == {}
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 2


def test_supported_negative_assessment_does_not_wait_for_unfinished_branding(database, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    monkeypatch.setattr(settings, "image_storage_enabled", True)
    with database.begin() as session:
        source = seed_source(session, logo="https://publisher.example/logo.png")
        identifier, output = source.id, assessment(source, approved=False, rejected=True)
        job_id = request_relevance(session, identifier).id
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: output)
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "rejected" and not source.enabled
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.scalar(select(ArticleImageJob.status)) == "queued"


def test_logo_worker_reconciles_admission_and_keeps_retry_intent_through_checkpoints(
    enabled, monkeypatch
):
    logo_backend = enabled
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    with logo_backend.begin() as session:
        source = seed_source(session, logo="https://publisher.example/logo.png")
        identifier, output = source.id, assessment(source)
        ai_id = request_relevance(session, source.id).id
        logo_id = session.scalar(select(ArticleImageJob.id))
    monkeypatch.setattr(source_tasks, "session_factory", lambda: logo_backend)
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: output)
    source_tasks.enrich_source(str(ai_id))
    process_logo(logo_backend, logo_id)
    with logo_backend.begin() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "approved" and source.enabled
        facts = source_readiness(session, source)
        refreshed = retry_source_processing(
            session, "source", identifier, facts.snapshot, "branding", logo_id
        )
        retry_id = refreshed.jobs["branding"].id
        assert not source.enabled and source.processing_request
    process_logo(logo_backend, retry_id)
    with logo_backend() as session:
        source, job = session.get(Source, identifier), session.get(ArticleImageJob, retry_id)
        assert job.status == "succeeded" and job.storage["source_pipeline_request"]
        assert source_readiness(session, source).readiness.stage == "paused"
        assert not source.enabled and source.processing_request
        assert "source_pipeline_request" not in source.managed_logo


def test_split_processing_migration_preserves_legacy_defaults_and_guards_rollback(database):
    migration = importlib.import_module("migrations.versions.0027_source_processing")
    with database.begin() as session:
        source = seed_source(session, approved=True, profile=False)
        # Omit only the new fields so PostgreSQL, rather than ORM defaults,
        # supplies the legacy compatibility values.
        session.execute(
            SourceEnrichmentJob.__table__.insert().values(source_id=source.id, status="succeeded")
        )
        session.execute(text("UPDATE source_enrichment_jobs SET operation=DEFAULT,result=DEFAULT"))
        row = session.execute(text("SELECT operation,result FROM source_enrichment_jobs")).one()
        assert row.operation == "legacy" and row.result == {}
        assert source.approval_status == "approved" and source.enabled
        job = request_enrichment(session, source.id)
        with Operations.context(MigrationContext.configure(session.connection())):
            with pytest.raises(RuntimeError, match="Drain source"):
                migration.downgrade()
            job.status = "succeeded"
            source.processing_request = {"required": ["profile", "branding"]}
            session.flush()
            with pytest.raises(RuntimeError, match="Resolve explicit"):
                migration.downgrade()


def test_reopening_requires_fresh_profile_and_manual_admission_without_scheduler_starvation(
    database, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "full_automation", True)
    monkeypatch.setattr(settings, "ai_enabled", True)
    with database.begin() as session:
        source = seed_source(session, profile=False)
        identifier, output = source.id, assessment(source)
        review_source(session, identifier, SourceDecision(decision="rejected", note="Not ready"))
        facts = source_readiness(session, source)
        assert facts.readiness.next_action == "reopen" and not facts.readiness.can_approve
        reopened = advance_source_processing(
            session, "source", identifier, facts.snapshot, actor={"subject": "editor"}
        )
        profile_id = reopened.jobs["profile"].id
        assert source.approval_status == "pending" and not source.enabled
        assert source.processing_request["actor"] == {"subject": "editor"}
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        with pytest.raises(OperationConflict, match="changed"):
            advance_source_processing(session, "source", identifier, facts.snapshot)
        with pytest.raises(OperationConflict, match="profile_"):
            review_source(session, identifier, SourceDecision(decision="approved"))
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *_: ({"_sample": SAMPLE}, None))
    monkeypatch.setattr(source_tasks, "assess_source", lambda *a, **kw: output)
    source_tasks.enrich_source(str(profile_id))
    with database() as session:
        ai_id = source_readiness(session, session.get(Source, identifier)).jobs["assessment"].id
    source_tasks.enrich_source(str(ai_id))
    with database.begin() as session:
        source = session.get(Source, identifier)
        facts = source_readiness(session, source)
        assert facts.readiness.stage == "review" and facts.readiness.can_approve
        assert source.processing_request["manual_review"] and not source.enabled
        assert source.approval_status == "pending"
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
    assert schedule_pending_reviews(database, limit=1) == 0
    with database.begin() as session:
        other = Source(
            name="Next actionable source",
            feed_url="https://next.example/feed",
            source_type="publisher",
            approval_status="pending",
            enabled=False,
        )
        session.add(other)
    assert schedule_pending_reviews(database, limit=1) == 1
    with database.begin() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "pending" and not source.enabled
        review_source(session, identifier, SourceDecision(decision="approved", actor="editor"))
        assert source.processing_request == {} and source.enabled
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 2


def test_reopening_preserves_old_active_profile_lease_but_requires_a_fresh_successor(
    database, monkeypatch
):
    with database.begin() as session:
        source = seed_source(session, profile=False)
        identifier, job = source.id, request_enrichment(session, source.id)
        old_id, token = job.id, uuid.uuid4()
        job.status, job.lease_token, job.lease_until = (
            "running",
            token,
            utcnow() + timedelta(minutes=5),
        )
        review_source(session, identifier, SourceDecision(decision="rejected", note="Not ready"))
        facts = source_readiness(session, source)
        reopened = advance_source_processing(session, "source", identifier, facts.snapshot)
        assert reopened.jobs["profile"].id == old_id
        assert job.status == "running" and job.lease_token == token
        assert not reopened.readiness.can_approve and not source.enabled
    monkeypatch.setattr(source_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *_: ({"_sample": SAMPLE}, None))
    # Finish exactly the original owned delivery rather than stealing its lease.
    from devfeed_core.job_lifecycle import finish_job

    with database.begin() as session:
        source, job = session.get(Source, identifier), session.get(SourceEnrichmentJob, old_id)
        finish_job(job, "enriched", utcnow())
        session.flush()
        reconciled = reconcile_source_processing(session, source)
        fresh_id = reconciled.jobs["profile"].id
        assert fresh_id != old_id and not reconciled.readiness.can_approve
    source_tasks.enrich_source(str(fresh_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert source_readiness(session, source).readiness.can_approve
        assert not source.enabled and source.processing_request["manual_review"]


def test_reopening_rejected_website_queues_the_selected_linked_candidate_once(database):
    with database.begin() as session:
        source = Source(
            name="Website awaiting discovery",
            website_url="https://publisher.example/",
            source_type="publisher",
            approval_status="pending",
            enabled=False,
        )
        session.add(source)
        session.flush()
        candidates = [
            SourceCandidate(
                name=source.name,
                identity_url=f"https://publisher.example/{index}",
                source_id=source.id,
                status="rejected",
                approval_status="rejected",
            )
            for index in range(2)
        ]
        session.add_all(candidates)
        review_source(session, source.id, SourceDecision(decision="rejected", note="No feed"))
        facts = source_readiness(session, source)
        chosen = facts.candidate.id
        reopened = advance_source_processing(session, "source", source.id, facts.snapshot)
        assert reopened.readiness.stage == "discovery"
        assert reopened.jobs["discovery"].candidate_id == chosen
        assert reopened.candidate.status == reopened.candidate.approval_status == "pending"
        assert source.approval_status == "pending" and not source.enabled
        assert session.scalar(select(func.count()).select_from(SourceDiscoveryJob)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1


@pytest.mark.parametrize("canonical_approved", [False, True])
def test_reopened_website_duplicate_feed_retains_review_history_without_changing_canonical_source(
    database, canonical_approved
):
    from devfeed_core.discovery import handoff

    with database.begin() as session:
        canonical = seed_source(session, approved=canonical_approved)
        if not canonical_approved:
            review_source(
                session,
                canonical.id,
                SourceDecision(
                    decision="rejected", actor="canonical-editor", note="Existing policy"
                ),
            )
        article = Article(
            canonical_url="https://publisher.example/existing",
            url_hash="existing-publisher-article",
            title="Previously published Python article",
            publication_status="published",
            review_status="approved",
        )
        session.add(article)
        session.flush()
        origin = ArticleOrigin(
            article_id=article.id,
            source_id=canonical.id,
            entry_key="existing-entry",
            original_url=article.canonical_url,
        )
        old = Source(
            name="Historical publisher alias",
            website_url="https://publisher.example/about",
            source_type="publisher",
            approval_status="pending",
            enabled=False,
        )
        session.add_all([origin, old])
        session.flush()
        candidate = SourceCandidate(
            name=old.name,
            identity_url=old.website_url,
            source_id=old.id,
            status="rejected",
            approval_status="rejected",
        )
        session.add(candidate)
        review_source(
            session,
            old.id,
            SourceDecision(decision="rejected", actor="historic-editor", note="No verified feed"),
        )
        previous = session.scalar(select(SourceReview).where(SourceReview.source_id == old.id))
        history = previous.id, previous.actor, previous.note, previous.created_at
        old_id, canonical_id, article_id, origin_id = old.id, canonical.id, article.id, origin.id
        facts = source_readiness(session, old)
        advance_source_processing(
            session, "source", old.id, facts.snapshot, actor={"subject": "reopening-editor"}
        )
        feed = CandidateFeed(candidate_id=candidate.id, url=canonical.feed_url)
        session.add(feed)
        session.flush()
        candidate.selected_feed_id = feed.id
        handoff(session, candidate)
        session.flush()
        assert candidate.source_id == canonical.id and candidate.status == "linked"
    with database() as session:
        canonical = session.get(Source, canonical_id)
        assert session.get(Source, old_id) is None
        assert canonical.approval_status == ("approved" if canonical_approved else "rejected")
        assert canonical.enabled is canonical_approved and canonical.processing_request == {}
        retained = session.get(SourceReview, history[0])
        assert retained.source_id == canonical_id
        assert (retained.id, retained.actor, retained.note, retained.created_at) == history
        assert session.scalar(select(func.count()).select_from(SourceReview)) == (
            1 if canonical_approved else 2
        )
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.get(Article, article_id).publication_status == "published"
        assert session.get(ArticleOrigin, origin_id).source_id == canonical_id
