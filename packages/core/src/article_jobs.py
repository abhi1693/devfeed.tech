"""Original-page enrichment outbox and exclusive leases. No HTTP in transactions."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, Source, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound


def approved_sources(session: Session, article_id: uuid.UUID, *, lock=False) -> list[uuid.UUID]:
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
        statement = statement.with_for_update(of=Source)
    return list(session.scalars(statement))


def request_article_enrichment(
    session: Session, article_id: uuid.UUID, *, automatic=False
) -> ArticleEnrichmentJob | None:
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == article_id)
        .with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
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
        return active
    if automatic and session.scalar(
        select(ArticleEnrichmentJob.id)
        .where(ArticleEnrichmentJob.article_id == article_id)
        .limit(1)
    ):
        return None  # Never re-fetch failed, empty or completed pages on every RSS poll.
    job = ArticleEnrichmentJob(article_id=article_id)
    session.add(job)
    session.flush()
    return job


def backfill_articles(session: Session, limit: int, *, source_id: uuid.UUID | None = None):
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    attempted = select(ArticleEnrichmentJob.id).where(ArticleEnrichmentJob.article_id == Article.id)
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


def claim_article(session: Session, job_id: uuid.UUID):
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
    start_job(job, now, LEASE_SECONDS)
    return job, article.canonical_url


def retry_article(session: Session, job_id: uuid.UUID):
    job = session.get(ArticleEnrichmentJob, job_id)
    if job is None:
        raise RecordNotFound("Article enrichment job not found")
    if job.status != "failed":
        raise OperationConflict(
            "Only failed jobs can be retried; use articles enrich for a new lookup"
        )
    return request_article_enrichment(session, job.article_id)


def prepare_article_dispatch(session: Session, job_id: uuid.UUID):
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
