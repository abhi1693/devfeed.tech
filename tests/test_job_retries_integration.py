"""All operations retry through the same eligibility and coalescing rules."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from devfeed_admin_api.jobs import MODELS
from devfeed_core.config import get_settings
from devfeed_core.job_retries import retry_failed_job
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    IngestionJob,
    NotificationDelivery,
    Source,
    SourceEnrichmentJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from devfeed_core.services import OperationConflict
from devfeed_core.urls import fingerprint
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@pytest.fixture
def failed_jobs(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://example.com/feed",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
        )
        article = Article(
            title="An engineering guide",
            canonical_url="https://example.com/guide",
            url_hash=fingerprint("https://example.com/guide"),
            summary="A detailed guide to building software and testing distributed systems. " * 5,
        )
        topics = [
            Topic(name=name, slug=name.lower(), kind="technology", status="active")
            for name in ["React", "JavaScript"]
        ]
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="new-topic",
            action="create",
            origin="import",
            source_name="Test",
            proposed={"name": "New topic", "slug": "new-topic", "kind": "technology"},
            created_by={"subject": "test"},
        )
        session.add_all([source, article, proposal, *topics])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="guide",
                original_url=article.canonical_url,
            )
        )
        failed = {
            "status": "failed",
            "attempts": 3,
            "error": "original failure",
            "finished_at": utcnow(),
        }
        research = {
            "input_hash": "a" * 64,
            "input_snapshot": {},
            "requested_by": {"subject": "original"},
            "prompt_version": "test",
        }
        jobs = {
            "ingestion": IngestionJob(source_id=source.id, **failed),
            "article-enrichment": ArticleEnrichmentJob(article_id=article.id, **failed),
            "images": ArticleImageJob(article_id=article.id, **failed),
            "source-enrichment": SourceEnrichmentJob(source_id=source.id, **failed),
            "analysis": ArticleAnalysisJob(article_id=article.id, **failed),
            "topic-analysis": TopicAnalysisJob(proposal_id=proposal.id, **research, **failed),
            "relationships": TopicAnalysisJob(
                topic_id=topics[0].id,
                **{**research, "input_snapshot": {"related_topic_id": str(topics[1].id)}},
                **failed,
            ),
            "notifications": NotificationDelivery(
                event_key="event",
                dedup_key="d" * 64,
                audience="admin",
                category="pipeline.failed",
                payload={"title": "Failure"},
                **failed,
            ),
        }
        session.add_all(jobs.values())
        session.flush()
        return {name: job.id for name, job in jobs.items()}


@pytest.mark.parametrize(
    "kind",
    [
        "ingestion",
        "article-enrichment",
        "images",
        "source-enrichment",
        "analysis",
        "topic-analysis",
        "relationships",
        "notifications",
    ],
)
def test_retry_queues_work_preserves_history_and_coalesces(
    admin_client, database, failed_jobs, kind
):
    pipeline = "topic-analysis" if kind == "relationships" else kind
    identifier = failed_jobs[kind]
    path = f"/v1/admin/jobs/{pipeline}/{identifier}/retry"
    result = admin_client.post(path)
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["kind"] == pipeline and value["status"] == "queued" and value["attempts"] == 0
    assert value["error"] is None
    queued_id = uuid.UUID(value["id"])
    with database() as session:
        original = session.get(MODELS[pipeline], identifier)
        queued = session.get(MODELS[pipeline], queued_id)
        if kind == "notifications":
            assert queued_id == identifier and queued.dedup_key == "d" * 64
            assert queued.event_key == "event" and queued.payload == {"title": "Failure"}
        else:
            assert queued_id != identifier
            assert (
                original.status == "failed"
                and original.attempts == 3
                and original.error == "original failure"
            )
        if pipeline == "topic-analysis":
            assert queued.requested_by["subject"] == "integration-admin"
            assert queued.requested_by["organization_id"] == "integration-org"
        if kind == "relationships":
            assert (
                queued.input_snapshot["related_topic_id"]
                == original.input_snapshot["related_topic_id"]
            )
    assert admin_client.post(path).status_code == 409
    assert admin_client.post(f"/v1/admin/jobs/{pipeline}/{queued_id}/retry").status_code == 409
    listings = [f"/v1/admin/jobs/{pipeline}"]
    if pipeline in {"analysis", "topic-analysis"}:
        listings.append("/v1/admin/jobs/ai-analysis")
    for listing in listings:
        candidates = admin_client.get(listing, params={"retryable_only": True}).json()["items"]
        assert str(identifier) not in {item["id"] for item in candidates}
    old = admin_client.get(f"/v1/admin/jobs/{pipeline}/{identifier}").json()
    assert not old["retryable"]
    assert old["status"] == ("queued" if kind == "notifications" else "retried")
    # A retry which fails becomes the only eligible failure for this subject.
    with database.begin() as session:
        queued = session.get(MODELS[pipeline], queued_id)
        queued.status = "failed"
        queued.finished_at = utcnow()
    candidates = admin_client.get(
        f"/v1/admin/jobs/{pipeline}", params={"status": "failed", "retryable_only": True}
    ).json()["items"]
    assert str(queued_id) in {item["id"] for item in candidates}
    if kind != "notifications":
        assert str(identifier) not in {item["id"] for item in candidates}
        assert admin_client.post(path).status_code == 409
        retired = admin_client.get(
            f"/v1/admin/jobs/{pipeline}", params={"status": "retried"}
        ).json()["items"]
        assert str(identifier) in {item["id"] for item in retired}


@pytest.mark.parametrize(
    "reason,kind",
    [
        ("disabled-source", "ingestion"),
        ("rejected-source", "source-enrichment"),
        ("no-origin", "article-enrichment"),
        ("existing-image", "images"),
        ("rejected-article", "analysis"),
        ("disabled-ai", "analysis"),
        ("reviewed-proposal", "topic-analysis"),
        ("inactive-topic", "relationships"),
        ("expired-delivery", "notifications"),
    ],
)
def test_ineligible_retry_changes_no_job(
    admin_client, database, failed_jobs, monkeypatch, reason, kind
):
    pipeline = "topic-analysis" if kind == "relationships" else kind
    with database.begin() as session:
        job = session.get(MODELS[pipeline], failed_jobs[kind])
        if reason == "disabled-source":
            session.get(Source, job.source_id).enabled = False
        elif reason == "rejected-source":
            session.get(Source, job.source_id).approval_status = "rejected"
        elif reason == "no-origin":
            session.scalar(select(Source)).approval_status = "rejected"
        elif reason == "existing-image":
            session.get(Article, job.article_id).image_url = "https://example.com/image.png"
        elif reason == "rejected-article":
            session.get(Article, job.article_id).review_status = "rejected"
        elif reason == "disabled-ai":
            monkeypatch.setattr(get_settings(), "ai_enabled", False)
        elif reason == "reviewed-proposal":
            proposal = session.get(TopicProposal, job.proposal_id)
            proposal.status = "rejected"
            proposal.reviewed_at = utcnow()
            proposal.reviewed_by = {"subject": "reviewer"}
        elif reason == "inactive-topic":
            session.get(Topic, job.topic_id).status = "proposed"
        elif reason == "expired-delivery":
            job.created_at = utcnow() - timedelta(days=29)
        before = session.scalar(select(func.count()).select_from(MODELS[pipeline]))
    result = admin_client.post(f"/v1/admin/jobs/{pipeline}/{failed_jobs[kind]}/retry")
    assert result.status_code in {404, 409}, result.text
    with database() as session:
        assert session.get(MODELS[pipeline], failed_jobs[kind]).status == "failed"
        assert session.scalar(select(func.count()).select_from(MODELS[pipeline])) == before


def test_concurrent_retries_coalesce_on_the_subject(database, failed_jobs):
    def retry():
        with database.begin() as session:
            original = session.get(ArticleEnrichmentJob, failed_jobs["article-enrichment"])
            try:
                return retry_failed_job(
                    session, original, "article-enrichment", {"subject": "admin"}
                ).id
            except OperationConflict:
                return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        identifiers = list(pool.map(lambda _: retry(), range(3)))
    assert len({identifier for identifier in identifiers if identifier is not None}) == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleEnrichmentJob)) == 2


def test_missing_and_invalid_job_types_are_rejected(admin_client):
    assert admin_client.post(f"/v1/admin/jobs/analysis/{uuid.uuid4()}/retry").status_code == 404
    assert admin_client.post(f"/v1/admin/jobs/unknown/{uuid.uuid4()}/retry").status_code == 422


def test_concurrent_notification_retry_does_not_reset_an_already_queued_delivery(
    database, failed_jobs
):
    barrier = Barrier(2)

    def retry():
        with database.begin() as session:
            original = session.get(NotificationDelivery, failed_jobs["notifications"])
            barrier.wait(timeout=5)
            try:
                retry_failed_job(session, original, "notifications", {"subject": "admin"})
                return "queued"
            except OperationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))
    assert sorted(results) == ["conflict", "queued"]


@pytest.mark.parametrize("newer_status", ["queued", "running", "succeeded", "failed"])
def test_preexisting_runs_exclude_old_failures_before_pagination(
    admin_client, database, failed_jobs, newer_status
):
    with database.begin() as session:
        old = session.get(ArticleAnalysisJob, failed_jobs["analysis"])
        newer = ArticleAnalysisJob(article_id=old.article_id, status=newer_status)
        session.add(newer)
        session.flush()
        newer_id = newer.id
    for listing in ("/v1/admin/jobs/analysis", "/v1/admin/jobs/ai-analysis"):
        result = admin_client.get(
            listing, params={"status": "failed", "analysis_type": "articles", "limit": 1}
        ).json()
        assert result["total"] == (1 if newer_status == "failed" else 0)
        assert [item["id"] for item in result["items"]] == (
            [str(newer_id)] if newer_status == "failed" else []
        )
        history = admin_client.get(
            listing, params={"status": "retried", "analysis_type": "articles"}
        ).json()
        assert history["total"] == 1
        assert history["items"][0]["id"] == str(failed_jobs["analysis"])
        assert history["items"][0]["status"] == "retried"
    assert (
        admin_client.post(f"/v1/admin/jobs/analysis/{failed_jobs['analysis']}/retry").status_code
        == 409
    )
