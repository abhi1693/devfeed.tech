"""Image lookup outbox and leases. Callers own transactions; no network I/O here."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.models import Article, ArticleImageJob, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound


def request_image(
    session: Session, article_id: uuid.UUID, *, automatic=False
) -> ArticleImageJob | None:
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == article_id)
        .with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    if article.image_url:
        return None
    active = session.scalar(
        select(ArticleImageJob).where(
            ArticleImageJob.article_id == article_id,
            ArticleImageJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        return active
    if automatic and session.scalar(
        select(ArticleImageJob.id).where(ArticleImageJob.article_id == article_id).limit(1)
    ):
        return None  # A missing image/terminal failure must not be retried on every RSS poll.
    job = ArticleImageJob(article_id=article_id)
    session.add(job)
    session.flush()
    return job


def backfill_images(session: Session, limit: int) -> list[ArticleImageJob]:
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    attempted = select(ArticleImageJob.id).where(ArticleImageJob.article_id == Article.id)
    identifiers = session.scalars(
        select(Article.id)
        .where(Article.image_url.is_(None), ~attempted.exists())
        .order_by(Article.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    jobs = []
    for identifier in identifiers:
        job = request_image(session, identifier, automatic=True)
        if job is not None:
            jobs.append(job)
    return jobs


def retry_image(session: Session, job_id: uuid.UUID) -> ArticleImageJob | None:
    previous = session.get(ArticleImageJob, job_id)
    if previous is None:
        raise RecordNotFound("Image job not found")
    if previous.status != "failed":
        raise OperationConflict(
            "Only failed image jobs can be retried; use images fetch for a new lookup"
        )
    return request_image(session, previous.article_id)


def claim_image(session: Session, job_id: uuid.UUID) -> tuple[ArticleImageJob, str] | None:
    # Targeted claims wait for dispatch to commit; a locked row is not a missing job.
    job = session.scalar(
        select(ArticleImageJob).where(ArticleImageJob.id == job_id).with_for_update()
    )
    now = utcnow()
    if job is None or job.status != "queued" or job.available_at > now:
        return None
    article = session.get(Article, job.article_id, options=[lazyload("*")])
    if article is None:
        return None  # FK cascade removes jobs for deleted articles.
    if article.image_url:
        finish_job(job, "already_present", utcnow())
        return None
    start_job(job, now, LEASE_SECONDS)
    return job, article.canonical_url


def prepare_image_dispatch(session: Session, job_id: uuid.UUID) -> ArticleImageJob:
    job = session.scalar(
        select(ArticleImageJob).where(ArticleImageJob.id == job_id).with_for_update()
    )
    if job is None:
        raise RecordNotFound("Image job not found")
    if job.status != "queued":
        raise OperationConflict(
            "Only queued image jobs can be dispatched; running jobs keep their lease"
        )
    job.available_at = utcnow()
    job.dispatched_at = None
    session.flush()
    return job
