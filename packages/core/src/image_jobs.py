"""Image lookup outbox and leases. Callers own transactions; no network I/O here."""

import uuid
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, lazyload

from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.managed_images import THUMBNAIL_VERSION, image_variants
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
    store = bool(article.image_url and get_settings().image_storage_enabled)
    if article.image_url and (not store or image_variants(article)):
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
        select(ArticleImageJob.id)
        .where(
            ArticleImageJob.article_id == article_id,
            ArticleImageJob.operation == ("store" if store else "discover"),
            *([ArticleImageJob.image_url == article.image_url] if store else []),
        )
        .limit(1)
    ):
        return None  # A missing image/terminal failure must not be retried on every RSS poll.
    job = ArticleImageJob(
        article_id=article_id,
        operation="store" if store else "discover",
        image_url=article.image_url if store else None,
    )
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
    from devfeed_core.logos import logo_job_target, request_logo

    previous = session.get(ArticleImageJob, job_id)
    if previous is None:
        raise RecordNotFound("Image job not found")
    if previous.status != "failed":
        raise OperationConflict(
            "Only failed image jobs can be retried; use images fetch for a new lookup"
        )
    target = logo_job_target(previous)
    if target:
        job = request_logo(
            session,
            target.model,
            getattr(previous, target.job_field),
            refresh=previous.operation == "source-logo-refresh",
        )
    else:
        assert previous.article_id is not None
        job = (
            request_thumbnail_recompression(session, previous.article_id)
            if previous.storage_version == THUMBNAIL_VERSION
            else request_image(session, previous.article_id)
        )
    if (
        job is not None
        and previous.operation in {"store", "topic-logo", "source-logo", "source-logo-refresh"}
        and job.operation == previous.operation
        and job.storage_version == previous.storage_version
        and previous.image_url == job.image_url
        and (
            not job.storage
            or (
                job.storage_version == THUMBNAIL_VERSION
                and job.operation == "store"
                and job.status == "queued"
                and not job.storage.get("variants")
            )
        )
    ):
        job.storage = dict(previous.storage or {})
    return job


def claim_image(session: Session, job_id: uuid.UUID) -> tuple[ArticleImageJob, str] | None:
    from devfeed_core.logos import (
        LOGO_VERSION,
        logo_current,
        logo_job_target,
        logo_target,
        request_logo,
    )
    from devfeed_core.models import Source, Topic

    # Match saves/completion by locking the logo subject before its job.
    # Article-only jobs do not match either query.
    for model in (Topic, Source):
        target = logo_target(model)
        session.scalar(
            select(model.id)
            .join(ArticleImageJob, target.job_column == model.id)
            .where(ArticleImageJob.id == job_id)
            .with_for_update(of=model)
        )
    # Targeted claims wait for dispatch to commit; a locked row is not a missing job.
    job = session.scalar(
        select(ArticleImageJob).where(ArticleImageJob.id == job_id).with_for_update()
    )
    now = utcnow()
    if job is None or job.status != "queued" or job.available_at > now:
        return None
    target = logo_job_target(job)
    if target:
        if not get_settings().image_storage_enabled:
            return None
        subject = cast(
            Topic | Source | None, session.get(target.model, getattr(job, target.job_field))
        )
        if subject is None:
            return None
        if (
            subject.logo_url != job.image_url
            or job.storage_version != LOGO_VERSION
            or (logo_current(subject) and job.operation != "source-logo-refresh")
        ):
            finish_job(job, "already_present", now)
            session.flush()
            request_logo(session, target.model, subject.id, automatic=True)
            return None
        start_job(job, now, LEASE_SECONDS)
        assert job.image_url is not None
        return job, job.image_url
    article = session.get(Article, job.article_id, options=[lazyload("*")])
    if article is None:
        return None  # FK cascade removes jobs for deleted articles.
    if job.operation == "store":
        if not get_settings().image_storage_enabled:
            return None
        already_stored = bool(image_variants(article)) and (
            job.storage_version != THUMBNAIL_VERSION
            or (article.managed_image or {}).get("thumbnail_version", "v1") == THUMBNAIL_VERSION
        )
        if article.image_url != job.image_url or already_stored:
            finish_job(job, "already_present", now)
            return None
        start_job(job, now, LEASE_SECONDS)
        assert job.image_url
        return job, job.image_url
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


def backfill_storage(session: Session, limit: int) -> list[ArticleImageJob]:
    """Resume using durable jobs, skipping previous attempts for the same source URL."""
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    if not get_settings().image_storage_enabled:
        raise OperationConflict("Enable image storage before scheduling the backfill")
    attempted = select(ArticleImageJob.id).where(
        ArticleImageJob.article_id == Article.id,
        ((ArticleImageJob.operation == "store") & (ArticleImageJob.image_url == Article.image_url))
        | ArticleImageJob.status.in_(["queued", "running"]),
    )
    identifiers = session.scalars(
        select(Article.id)
        .where(
            Article.image_url.is_not(None),
            ~attempted.exists(),
            (
                func.coalesce(Article.managed_image["source_url"].as_string(), "")
                != Article.image_url
            )
            | (func.coalesce(Article.managed_image["version"].as_string(), "") != "v1"),
        )
        .order_by(
            (Article.publication_status == "published").desc(), Article.feed_at.desc(), Article.id
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    return [
        job
        for identifier in identifiers
        if (job := request_image(session, identifier, automatic=True)) is not None
    ]


def request_thumbnail_recompression(
    session: Session, article_id: uuid.UUID
) -> ArticleImageJob | None:
    """Reuse a saved original; keep the published asset until every new variant is ready."""
    article = session.scalar(
        select(Article).where(Article.id == article_id).with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    asset = article.managed_image or {}
    if (
        not image_variants(article)
        or asset.get("thumbnail_version", "v1") != "v1"
        or not asset.get("original_key")
        or not asset.get("hash")
        or not asset.get("source_width")
    ):
        return None
    active = session.scalar(
        select(ArticleImageJob).where(
            ArticleImageJob.article_id == article_id,
            ArticleImageJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        return active
    job = ArticleImageJob(
        article_id=article_id,
        operation="store",
        image_url=article.image_url,
        storage_version=THUMBNAIL_VERSION,
        storage={**asset, "thumbnail_version": THUMBNAIL_VERSION, "variants": []},
    )
    session.add(job)
    session.flush()
    return job


def backfill_thumbnail_encoding(session: Session, limit: int) -> list[ArticleImageJob]:
    """Bounded, idempotent encoding upgrade; failed attempts require explicit retry."""
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    if not get_settings().image_storage_enabled:
        raise OperationConflict("Enable image storage before scheduling recompression")
    attempted = select(ArticleImageJob.id).where(
        ArticleImageJob.article_id == Article.id,
        ArticleImageJob.status.in_(["queued", "running"])
        | (
            (ArticleImageJob.storage_version == THUMBNAIL_VERSION)
            & (ArticleImageJob.image_url == Article.image_url)
        ),
    )
    identifiers = session.scalars(
        select(Article.id)
        .where(
            Article.image_url.is_not(None),
            Article.managed_image["source_url"].as_string() == Article.image_url,
            Article.managed_image["original_key"].as_string().is_not(None),
            func.coalesce(Article.managed_image["thumbnail_version"].as_string(), "v1") == "v1",
            ~attempted.exists(),
        )
        .order_by(
            (Article.publication_status == "published").desc(), Article.feed_at.desc(), Article.id
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    return [
        job
        for identifier in identifiers
        if (job := request_thumbnail_recompression(session, identifier)) is not None
    ]
