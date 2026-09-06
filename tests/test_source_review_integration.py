import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler, source_tasks, tasks
from devfeed_core import services
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import (
    Article,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    SourceReview,
    utcnow,
)
from devfeed_core.schemas import SourceCreate, SourceDecision
from devfeed_core.source_enrichment import request_enrichment
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def submitted(client):
    result = client.post(
        "/v1/sources",
        json={
            "feed_url": "https://example.com/rss",
            "source_type": "publisher",
            "submitted_by": {"name": "Contributor", "profile_url": "https://example.com/person"},
        },
    )
    assert result.status_code == 201, result.text
    return uuid.UUID(result.json()["id"])


def test_external_submission_stays_pending_and_private_until_operator_approval(database, client):
    identifier = submitted(client)
    assert client.get("/v1/sources").json() == []
    assert client.get(f"/v1/sources/{identifier}").status_code == 404
    assert scheduler.tick()["scheduled"] == 0
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "pending" and source.submission_channel == "api"
        assert source.submitted_by["name"] == "Contributor"
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0
    with database.begin() as session:
        services.review_source(
            session, identifier, SourceDecision(decision="approved", actor="Operator")
        )
    public = client.get(f"/v1/sources/{identifier}").json()
    assert public["id"] == str(identifier) and "logo_url" in public
    assert (
        not {"submitted_by", "reviewed_by", "review_note", "last_error", "feed_url"} & public.keys()
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 1
        assert session.scalar(select(SourceReview.actor)) == "Operator"


def test_duplicate_api_or_cli_submission_cannot_change_attribution_or_approve_pending(
    database, client
):
    identifier = submitted(client)
    assert (
        client.post(
            "/v1/sources",
            json={
                "feed_url": "https://example.com/rss",
                "source_type": "publisher",
                "submitted_by": {"name": "Imposter"},
            },
        ).status_code
        == 409
    )
    validated = services.validate_source(
        SourceCreate(
            feed_url="https://example.com/rss",
            source_type="publisher",
            submitted_by={"name": "Replacement"},
        )
    )
    with database.begin() as session:
        source, created, job = services.submit_source(session, validated)
        assert source.id == identifier and not created and job is None
        assert source.approval_status == "pending" and source.submitted_by["name"] == "Contributor"


def test_rejection_after_claim_blocks_commit_and_reapproval_preserves_history(
    database, client, monkeypatch
):
    identifier = submitted(client)
    with database.begin() as session:
        services.review_source(session, identifier, SourceDecision(decision="approved"))
        job_id = services.fetch_source(session, identifier).id

    def reject_while_downloading(*a):
        with database.begin() as session:
            services.review_source(
                session, identifier, SourceDecision(decision="rejected", note="Review withdrawn")
            )
        return FetchResult(304, b"", "https://example.com/rss")

    monkeypatch.setattr(tasks, "fetch_feed", reject_while_downloading)
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.get(IngestionJob, job_id).status == "failed"
        assert session.get(Source, identifier).consecutive_failures == 0
        assert session.scalar(select(func.count()).select_from(Article)) == 0
    with database.begin() as session:
        services.review_source(
            session, identifier, SourceDecision(decision="approved", note="Fixed")
        )
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 3
        assert services.fetch_source(session, identifier).id != job_id


def test_concurrent_approval_creates_one_review_and_one_active_run(database, client):
    identifier = submitted(client)

    def approve(_):
        with database.begin() as session:
            services.review_source(session, identifier, SourceDecision(decision="approved"))

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(approve, range(3)))
    with database() as session:
        for model in (SourceReview, IngestionJob, SourceEnrichmentJob):
            assert session.scalar(select(func.count()).select_from(model)) == 1


def test_profile_enrichment_is_independent_of_review_and_does_not_overwrite_edits(
    database, client, monkeypatch
):
    identifier = submitted(client)
    with database.begin() as session:
        job_id = request_enrichment(session, identifier).id

    def lookup(*args):
        with database.begin() as session:
            session.get(Source, identifier).description = "Manual override"
        return {"description": "Fetched", "logo_url": "https://cdn.example/logo.png"}, None

    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert (
            source.description == "Manual override"
            and source.logo_url == "https://cdn.example/logo.png"
        )
        assert source.approval_status == "pending" and source.last_success_at is None
        assert session.get(SourceEnrichmentJob, job_id).status == "succeeded"
    assert client.get("/v1/sources").json() == []


def test_source_enrichment_stale_owner_cannot_commit(database, client, monkeypatch):
    identifier = submitted(client)
    with database.begin() as session:
        job_id = request_enrichment(session, identifier).id

    def lookup(*args):
        with database.begin() as session:
            job = session.get(SourceEnrichmentJob, job_id)
            job.lease_token = uuid.uuid4()
            job.lease_until = utcnow() + timedelta(minutes=5)
        return {"logo_url": "https://cdn.example/logo.png"}, None

    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        assert session.get(Source, identifier).logo_url is None
        assert session.get(SourceEnrichmentJob, job_id).status == "running"
