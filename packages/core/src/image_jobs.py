"""Image lookup outbox and leases. Callers own transactions; no network I/O here."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, lazyload

from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.json_types import JsonRecord
from devfeed_core.managed_images import (
    ARTICLE_THUMBNAIL_LAYOUT,
    IMAGE_VERSION,
    THUMBNAIL_VERSION,
    image_variants,
)
from devfeed_core.models import Article, ArticleImageJob, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound


def thumbnail_maintenance_current(article: Article, job: ArticleImageJob) -> bool:
    """A derivative upgrade may reuse the published article's unchanged saved original."""
    checkpoint = job.storage or {}
    asset = article.managed_image or {}
    width = asset.get("source_width")
    return bool(
        job.operation == "store"
        and job.storage_version == THUMBNAIL_VERSION
        and not checkpoint.get("article_refresh")
        and asset.get("version") == IMAGE_VERSION
        and asset.get("variants")
        and asset.get("source_url") == article.image_url
        and checkpoint.get("version") == IMAGE_VERSION
        and checkpoint.get("thumbnail_version") == THUMBNAIL_VERSION
        and job.image_url == article.image_url == checkpoint.get("source_url")
        and asset.get("hash")
        and asset.get("original_key")
        and type(width) is int
        and width > 0
        and all(
            checkpoint.get(field) == asset.get(field)
            for field in ("hash", "original_key", "source_width")
        )
    )


def request_image(
    session: Session, article_id: uuid.UUID, *, automatic: bool = False, refresh: bool = False
) -> ArticleImageJob | None:
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == article_id)
        .with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    if article.publication_status == "published":
        if automatic:
            return None
        raise OperationConflict("Unpublish the article before requesting image processing")
    store = bool(article.image_url and get_settings().image_storage_enabled)
    if article.image_url and (not store or image_variants(article)) and not refresh:
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
            *(
                [ArticleImageJob.image_url == article.image_url]
                if store
                else [ArticleImageJob.storage["input_url"].as_string() == article.canonical_url]
            ),
        )
        .limit(1)
    ):
        return None  # A missing image/terminal failure must not be retried on every RSS poll.
    job = ArticleImageJob(
        article_id=article_id,
        operation="store" if store else "discover",
        image_url=article.image_url if store else None,
        storage={
            **({"input_url": article.canonical_url} if not store else {}),
            **({"article_refresh": True} if refresh else {}),
            **({"previous_image_url": article.image_url} if refresh and not store else {}),
        },
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


def retry_image(
    session: Session, job_id: uuid.UUID, *, refresh: bool = False
) -> ArticleImageJob | None:
    from devfeed_core.logos import logo_job_target, request_logo
    from devfeed_core.models import PartnerProduct

    previous = session.get(ArticleImageJob, job_id)
    if previous is None:
        raise RecordNotFound("Image job not found")
    if previous.status != "failed":
        raise OperationConflict(
            "Only failed image jobs can be retried; use images fetch for a new lookup"
        )
    target = logo_job_target(previous)
    if previous.operation == "product-ad-image":
        from devfeed_core.partner_product_images import request_product_ad_image

        assert previous.product_id is not None and previous.account_id is not None
        if previous.storage.get("cleanup_upload"):
            from devfeed_core.models import PartnerProductPromotion
            from devfeed_core.partner_product_images import request_ad_image_cleanup

            session.scalar(
                select(PartnerProduct.id)
                .where(PartnerProduct.id == previous.product_id)
                .with_for_update()
            )
            policy = session.get(
                PartnerProductPromotion, (previous.account_id, previous.product_id)
            )
            job = request_ad_image_cleanup(session, policy) if policy else None
        else:
            job = request_product_ad_image(session, previous.account_id, previous.product_id)
    elif previous.operation == "product-image":
        from devfeed_core.partner_product_images import request_product_image

        assert previous.product_id is not None
        job = request_product_image(session, previous.product_id)
    elif target:
        job = request_logo(
            session,
            target.model,
            getattr(previous, target.job_field),
            refresh=previous.operation == "source-logo-refresh",
        )
    else:
        assert previous.article_id is not None
        if (previous.storage or {}).get("thumbnail_layout") == ARTICLE_THUMBNAIL_LAYOUT:
            job = request_thumbnail_resize(
                session, previous.article_id, checkpoint=previous.storage
            )
        else:
            if previous.storage_version == THUMBNAIL_VERSION:
                job = request_thumbnail_recompression(session, previous.article_id)
            elif refresh or (previous.storage or {}).get("article_refresh"):
                job = request_image(session, previous.article_id, refresh=True)
            else:
                job = request_image(session, previous.article_id)
    if (
        job is not None
        and previous.operation
        in {
            "store",
            "topic-logo",
            "source-logo",
            "source-logo-refresh",
            "product-logo",
            "product-image",
            "product-ad-image",
        }
        and job.operation == previous.operation
        and job.storage_version == previous.storage_version
        and previous.image_url == job.image_url
        and previous.subject_revision == job.subject_revision
        and (
            not job.storage
            or job.storage == {"article_refresh": True}
            or (
                job.storage_version == THUMBNAIL_VERSION
                and job.operation == "store"
                and job.status == "queued"
                and not job.storage.get("variants")
            )
        )
    ):
        job.storage = {
            **(previous.storage or {}),
            **({"article_refresh": True} if (job.storage or {}).get("article_refresh") else {}),
        }
    return job


def claim_image(session: Session, job_id: uuid.UUID) -> tuple[ArticleImageJob, str] | None:
    from devfeed_core.logos import (
        LOGO_VERSION,
        logo_current,
        logo_job_target,
        logo_source,
        logo_target,
        request_logo,
    )
    from devfeed_core.models import PartnerProduct, PartnerProductPromotion, Source, Topic

    # Match saves/completion by locking the logo subject before its job.
    # Article-only jobs do not match either query.
    for model in (Topic, Source, PartnerProduct):
        locking_target = logo_target(model)
        session.scalar(
            select(model.id)
            .join(ArticleImageJob, locking_target.job_column == model.id)
            .where(ArticleImageJob.id == job_id)
            .with_for_update(of=model)
        )
    session.scalar(
        select(PartnerProductPromotion)
        .join(
            ArticleImageJob,
            (ArticleImageJob.account_id == PartnerProductPromotion.account_id)
            & (ArticleImageJob.product_id == PartnerProductPromotion.product_id),
        )
        .where(ArticleImageJob.id == job_id)
        .with_for_update(of=PartnerProductPromotion)
    )
    session.scalar(
        select(Article.id)
        .join(ArticleImageJob, ArticleImageJob.article_id == Article.id)
        .where(ArticleImageJob.id == job_id)
        .with_for_update(of=Article)
    )
    # Targeted claims wait for dispatch to commit; a locked row is not a missing job.
    job = session.scalar(
        select(ArticleImageJob).where(ArticleImageJob.id == job_id).with_for_update()
    )
    now = utcnow()
    if job is None or job.status != "queued" or job.available_at > now:
        return None
    if job.operation == "product-ad-image":
        assert job.account_id is not None  # The promotion subject requires this FK.
        from devfeed_core.partner_product_images import product_ad_image_current

        if not get_settings().image_storage_enabled:
            return None
        product = session.get(PartnerProduct, job.product_id)
        policy = session.get(PartnerProductPromotion, (job.account_id, job.product_id))
        if job.storage.get("cleanup_upload"):
            from devfeed_core.partner_product_images import (
                uploaded_ad_image_prefix,
            )

            if (
                product is None
                or not product.merged_into_id
                or policy is None
                or uploaded_ad_image_prefix(policy.managed_ad_image, job.account_id)
                != job.storage["cleanup_upload"]
            ):
                finish_job(job, "already_present", now)
                return None
            start_job(job, now, LEASE_SECONDS)
            assert job.image_url is not None
            return job, job.image_url
        if (
            product is None
            or product.merged_into_id
            or policy is None
            or policy.ad_image_url != job.image_url
            or policy.ad_image_revision != job.subject_revision
            or product_ad_image_current(policy)
        ):
            finish_job(job, "already_present", now)
            return None
        start_job(job, now, LEASE_SECONDS)
        assert job.image_url is not None
        return job, job.image_url
    if job.operation == "product-image":
        from devfeed_core.partner_product_images import product_image_current, request_product_image

        if not get_settings().image_storage_enabled:
            return None
        product = session.get(PartnerProduct, job.product_id)
        if product is None:
            return None
        if (
            product.product_url != job.image_url
            or product_image_current(product)
            or product.excluded
            or product.merged_into_id
        ):
            finish_job(job, "already_present", now)
            session.flush()
            if not product.excluded and not product.merged_into_id:
                request_product_image(session, product.id, automatic=True)
            return None
        start_job(job, now, LEASE_SECONDS)
        assert job.image_url is not None
        return job, job.image_url
    target = logo_job_target(job)
    if target:
        if not get_settings().image_storage_enabled:
            return None
        subject = cast(
            Topic | Source | PartnerProduct | None,
            session.get(target.model, getattr(job, target.job_field)),
        )
        if subject is None:
            return None
        if (
            logo_source(subject) != job.image_url
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
    if job.operation == "store" and not get_settings().image_storage_enabled:
        return None
    if article.publication_status == "published" and not thumbnail_maintenance_current(
        article, job
    ):
        finish_job(job, "already_present", now)
        return None
    if job.operation == "store":
        resizing = (job.storage or {}).get("thumbnail_layout") == ARTICLE_THUMBNAIL_LAYOUT
        current_asset = article.managed_image or {}
        already_stored = bool(image_variants(article)) and (
            current_asset.get("thumbnail_layout") == ARTICLE_THUMBNAIL_LAYOUT
            if resizing
            else job.storage_version != THUMBNAIL_VERSION
            or current_asset.get("thumbnail_version", "v1") == THUMBNAIL_VERSION
        )
        if article.image_url != job.image_url or (
            already_stored and not (job.storage or {}).get("article_refresh")
        ):
            finish_job(job, "already_present", now)
            session.flush()
            request_image(session, article.id, automatic=True)
            return None
        start_job(job, now, LEASE_SECONDS)
        assert job.image_url
        return job, job.image_url
    if article.image_url and not (job.storage or {}).get("article_refresh"):
        finish_job(job, "already_present", utcnow())
        session.flush()
        if get_settings().image_storage_enabled:
            request_image(session, article.id, automatic=True)
        return None
    job.storage = {**(job.storage or {}), "input_url": article.canonical_url}
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


def request_thumbnail_resize(
    session: Session, article_id: uuid.UUID, *, checkpoint: JsonRecord | None = None
) -> ArticleImageJob | None:
    article = session.scalar(
        select(Article).where(Article.id == article_id).with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    asset = article.managed_image or {}
    if (
        not image_variants(article)
        or asset.get("thumbnail_version") != THUMBNAIL_VERSION
        or asset.get("thumbnail_layout") == ARTICLE_THUMBNAIL_LAYOUT
        or not all(asset.get(key) for key in ("original_key", "hash", "source_width"))
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
    if checkpoint and (
        checkpoint.get("source_url") != article.image_url
        or checkpoint.get("hash") != asset.get("hash")
    ):
        checkpoint = None
    job = ArticleImageJob(
        article_id=article.id,
        operation="store",
        image_url=article.image_url,
        storage_version=THUMBNAIL_VERSION,
        storage={**(checkpoint or asset), "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT},
    )
    session.add(job)
    session.flush()
    return job


def backfill_thumbnail_sizes(session: Session, limit: int) -> list[ArticleImageJob]:
    """Add card variants from saved v2 originals; terminal failures require explicit retry."""
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    if not get_settings().image_storage_enabled:
        raise OperationConflict("Enable image storage before scheduling thumbnail resizing")
    attempted = select(ArticleImageJob.id).where(
        ArticleImageJob.article_id == Article.id,
        ArticleImageJob.status.in_(["queued", "running"])
        | (
            (ArticleImageJob.storage["thumbnail_layout"].as_string() == ARTICLE_THUMBNAIL_LAYOUT)
            & (ArticleImageJob.image_url == Article.image_url)
        ),
    )
    articles = session.scalars(
        select(Article)
        .options(lazyload("*"))
        .where(
            Article.image_url.is_not(None),
            Article.managed_image["source_url"].as_string() == Article.image_url,
            Article.managed_image["version"].as_string() == "v1",
            Article.managed_image["thumbnail_version"].as_string() == THUMBNAIL_VERSION,
            Article.managed_image["original_key"].as_string().is_not(None),
            Article.managed_image["hash"].as_string().is_not(None),
            Article.managed_image["source_width"].as_integer() > 0,
            func.coalesce(Article.managed_image["thumbnail_layout"].as_string(), "")
            != ARTICLE_THUMBNAIL_LAYOUT,
            ~attempted.exists(),
        )
        .order_by(
            (Article.publication_status == "published").desc(), Article.feed_at.desc(), Article.id
        )
        .limit(limit)
        .with_for_update(skip_locked=True, of=Article)
    ).all()
    jobs = []
    for article in articles:
        job = request_thumbnail_resize(session, article.id)
        if job is not None:
            jobs.append(job)
    return jobs
