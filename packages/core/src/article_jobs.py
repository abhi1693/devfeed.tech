"""Original-page enrichment outbox and exclusive leases. No HTTP in transactions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, Source, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound

EXTRACTION_VERSION = "article-extraction-v2-full-evidence"


def approved_sources(
    session: Session, article_id: uuid.UUID, *, lock: bool = False
) -> list[uuid.UUID]:
    statement = (
        select(Source.id)
        .where(
            Source.id.in_(
                select(ArticleOrigin.source_id).where(ArticleOrigin.article_id == article_id)
            ),
            Source.approval_status == "approved",
        )
        .order_by(Source.id)
    )
    if lock:
        # Hold approval/policy stable without serializing unrelated articles
        # from the same source. SHARE still conflicts with review updates/deletes.
        statement = statement.with_for_update(of=Source, read=True)
    return list(session.scalars(statement))


def request_article_enrichment(
    session: Session,
    article_id: uuid.UUID,
    *,
    automatic: bool = False,
    refresh_id: uuid.UUID | None = None,
) -> ArticleEnrichmentJob | None:
    from devfeed_core.article_refresh import refresh_for_article, select_refresh_job

    if refresh_id is not None and not approved_sources(session, article_id, lock=True):
        raise OperationConflict("Article maintenance requires an approved source origin")
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == article_id)
        .with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    refresh = refresh_for_article(session, article, refresh_id, lock=True) if refresh_id else None
    if refresh_id is not None and refresh is None:
        raise OperationConflict("The maintenance candidate is no longer current")
    if article.publication_status == "published" and refresh is None:
        if automatic:
            return None
        raise OperationConflict("Use article maintenance to refresh a published article")
    # Ingestion inserts the origin before requesting enrichment, in the same transaction.
    if not approved_sources(session, article_id):
        raise OperationConflict("Article enrichment requires an approved source origin")
    active = session.scalar(
        select(ArticleEnrichmentJob).where(
            ArticleEnrichmentJob.article_id == article_id,
            ArticleEnrichmentJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        if active.refresh_id != refresh_id or (
            refresh is not None and active.id != refresh.enrichment_job_id
        ):
            if automatic:
                return None
            raise OperationConflict("Another enrichment task is still running for this article")
        return active
    if automatic and session.scalar(
        select(ArticleEnrichmentJob.id)
        .where(
            ArticleEnrichmentJob.article_id == article_id,
            ArticleEnrichmentJob.refresh_id.is_not_distinct_from(refresh_id),
        )
        .limit(1)
    ):
        return None  # Never re-fetch failed, empty or completed pages on every RSS poll.
    job = ArticleEnrichmentJob(
        article_id=article_id,
        refresh_id=refresh_id,
        result={"extraction_version": EXTRACTION_VERSION, "input_url": article.canonical_url},
    )
    session.add(job)
    session.flush()
    if refresh is not None:
        select_refresh_job(refresh, "enrichment", job)
    return job


def backfill_articles(
    session: Session, limit: int, *, source_id: uuid.UUID | None = None
) -> list[ArticleEnrichmentJob]:
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    from devfeed_core.article_refresh_jobs import ordinary_job_condition

    attempted = select(ArticleEnrichmentJob.id).where(
        ArticleEnrichmentJob.article_id == Article.id,
        ordinary_job_condition(ArticleEnrichmentJob),
    )
    origin = (
        select(ArticleOrigin.id)
        .join(Source, Source.id == ArticleOrigin.source_id)
        .where(ArticleOrigin.article_id == Article.id, Source.approval_status == "approved")
    )
    if source_id is not None:
        origin = origin.where(ArticleOrigin.source_id == source_id)
    identifiers = session.scalars(
        select(Article.id)
        .where(
            ~attempted.exists(),
            origin.exists(),
        )
        .order_by(Article.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    return [
        job
        for identifier in identifiers
        if (job := request_article_enrichment(session, identifier, automatic=True)) is not None
    ]


def claim_article(session: Session, job_id: uuid.UUID) -> tuple[ArticleEnrichmentJob, str] | None:
    from devfeed_core.article_refresh import refresh_for_article, refresh_job_current

    preview = session.scalar(select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.id == job_id))
    if preview is None:
        return None
    if preview.refresh_id is not None:
        approved_sources(session, preview.article_id, lock=True)
        session.scalar(
            select(Article)
            .options(lazyload("*"))
            .where(Article.id == preview.article_id)
            .with_for_update(of=Article)
        )
    # Wait out the dispatch transaction rather than consuming a still-locked job.
    job = session.scalar(
        select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.id == job_id).with_for_update()
    )
    now = utcnow()
    if job is None or job.status != "queued" or job.available_at > now:
        return None
    article = session.get(Article, job.article_id, options=[lazyload("*")])
    if article is None:
        return None
    if not approved_sources(session, article.id):
        finish_job(job, "unapproved", utcnow())
        return None
    if job.refresh_id is not None:
        refresh = refresh_for_article(session, article, job.refresh_id, lock=True)
        if refresh is None or not refresh_job_current(refresh, article, job, "enrichment"):
            finish_job(job, "superseded", now)
            return None
    elif article.publication_status == "published":
        finish_job(job, "superseded", now)
        return None
    start_job(job, now, LEASE_SECONDS)
    job.result = {**(job.result or {}), "extraction_version": EXTRACTION_VERSION}
    return job, article.canonical_url


def retry_article(session: Session, job_id: uuid.UUID) -> ArticleEnrichmentJob | None:
    job = session.get(ArticleEnrichmentJob, job_id)
    if job is None:
        raise RecordNotFound("Article enrichment job not found")
    if job.status != "failed":
        raise OperationConflict(
            "Only failed jobs can be retried; use articles enrich for a new lookup"
        )
    return request_article_enrichment(session, job.article_id, refresh_id=job.refresh_id)


def prepare_article_dispatch(session: Session, job_id: uuid.UUID) -> ArticleEnrichmentJob:
    job = session.scalar(
        select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.id == job_id).with_for_update()
    )
    if job is None:
        raise RecordNotFound("Article enrichment job not found")
    if job.status != "queued":
        raise OperationConflict(
            "Only queued article jobs can be dispatched; running jobs keep their lease"
        )
    job.available_at, job.dispatched_at = utcnow(), None
    session.flush()
    return job
