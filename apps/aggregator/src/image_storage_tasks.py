"""Checkpointed image storage shared by article photos and catalog logos."""

import logging
import uuid
from collections.abc import Callable, Mapping
from typing import cast

from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.image_jobs import thumbnail_maintenance_current
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, finish_job
from devfeed_core.jobs import owned_job
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, THUMBNAIL_VERSION
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    PartnerProduct,
    PartnerProductPromotion,
    Source,
    Topic,
    utcnow,
)
from devfeed_core.object_storage import R2Storage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devfeed_aggregator.image_storage import (
    ArticleThumbnailVariantStore,
    ImageOriginalStore,
    ThumbnailVariantStore,
    storage_client,
)
from devfeed_aggregator.image_storage_base import OriginalStore, StoredImage, VariantStore

type ImageSubject = Article | Topic | Source | PartnerProduct | PartnerProductPromotion
type LogoSubject = Topic | Source | PartnerProduct
type SubjectIdentity = uuid.UUID | tuple[uuid.UUID, uuid.UUID]

logger = logging.getLogger(__name__)


def checkpoint(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    asset: Mapping[str, object],
) -> bool:
    with factory.begin() as session:
        job = owned_job(session, ArticleImageJob, identifier, token)
        if job is None:
            return False
        marker = (job.storage or {}).get("source_pipeline_request")
        job.storage = {
            **asset,
            **({"source_pipeline_request": marker} if marker else {}),
            **({"article_refresh": True} if (job.storage or {}).get("article_refresh") else {}),
        }
    return True


def publish_logo_original[T: LogoSubject](
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    subject_id: SubjectIdentity,
    source: str,
    asset: Mapping[str, object],
    *,
    defer: bool = False,
    model: type[T],
) -> bool:
    """Expose a saved original while variants finish, preserving completed replacements."""
    with factory.begin() as session:
        subject = session.scalar(
            select(model).where(model.__table__.c.id == subject_id).with_for_update()
        )
        job = owned_job(session, ArticleImageJob, identifier, token)
        if job is None or subject is None:
            return False
        from devfeed_core.logos import logo_source

        if logo_source(subject) == source and not (subject.managed_logo or {}).get("variants"):
            subject.managed_logo = {**asset, "variants": []}
        if defer:
            job.status = "queued"
            job.available_at = utcnow()
            job.dispatched_at = None
            job.attempts = 0  # Variant generation has its own retry budget.
            job.error = None
            clear_lease(job)
    return True


def store_image(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    article_id: uuid.UUID,
    source: str,
) -> None:
    _store_asset(
        factory,
        identifier,
        token,
        article_id,
        source,
        model=Article,
        source_field="image_url",
        asset_field="managed_image",
        original_store=ImageOriginalStore,
        variant_store=ArticleThumbnailVariantStore,
        method="r2-imgproxy",
    )


def store_product_image(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    product_id: uuid.UUID,
    source: str,
) -> None:
    from devfeed_aggregator.product_image_storage import ProductImageOriginalStore

    _store_asset(
        factory,
        identifier,
        token,
        product_id,
        source,
        model=PartnerProduct,
        source_field="product_url",
        asset_field="managed_image",
        original_store=ProductImageOriginalStore,
        variant_store=ThumbnailVariantStore,
        method="r2-imgproxy",
    )


def store_product_ad_image(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    source: str,
) -> None:
    _store_asset(
        factory,
        identifier,
        token,
        (account_id, product_id),
        source,
        model=PartnerProductPromotion,
        source_field="ad_image_url",
        asset_field="managed_ad_image",
        original_store=ImageOriginalStore,
        variant_store=ThumbnailVariantStore,
        method="r2-imgproxy",
    )


def cleanup_product_ad_image(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    account_id: uuid.UUID,
    product_id: uuid.UUID,
) -> None:
    from devfeed_core.partner_product_images import (
        AD_IMAGE_SIZES,
        uploaded_ad_image_prefix,
    )

    with factory.begin() as session:
        policy = _lock_subject(session, PartnerProductPromotion, (account_id, product_id))
        job = owned_job(session, ArticleImageJob, identifier, token)
        product = session.get(PartnerProduct, product_id)
        if job is None or policy is None:
            return
        prefix = job.storage.get("cleanup_upload")
        if (
            product is None
            or not product.merged_into_id
            or prefix != uploaded_ad_image_prefix(policy.managed_ad_image, account_id)
        ):
            finish_job(job, "already_present", utcnow())
            return
        try:
            with storage_client() as client:
                for size in AD_IMAGE_SIZES:
                    client.delete(f"{prefix}/{size}.webp")
            policy.managed_ad_image = {}
            finish_job(job, "found", utcnow())
        except Exception as exc:
            fail_or_retry(job, f"Image cleanup failed: {type(exc).__name__}", utcnow())


def _lock_subject[T: ImageSubject](
    session: Session, model: type[T], subject_id: SubjectIdentity
) -> T | None:
    if model is PartnerProductPromotion:
        assert isinstance(subject_id, tuple)
        account_id, product_id = subject_id
        # Saves, claims, merges and publication use product -> policy -> job.
        session.scalar(
            select(PartnerProduct.id).where(PartnerProduct.id == product_id).with_for_update()
        )
        return session.scalar(
            select(model)
            .where(
                PartnerProductPromotion.account_id == account_id,
                PartnerProductPromotion.product_id == product_id,
            )
            .with_for_update()
        )
    return session.scalar(select(model).where(model.__table__.c.id == subject_id).with_for_update())


def _current_source(
    subject: ImageSubject, job: ArticleImageJob, source_field: str, source: str
) -> bool:
    return (
        (
            not isinstance(subject, Article)
            or subject.publication_status != "published"
            or thumbnail_maintenance_current(subject, job)
        )
        and getattr(subject, source_field) == source
        and (
            not isinstance(subject, PartnerProductPromotion)
            or subject.ad_image_revision == job.subject_revision
        )
    )


def publish_product_image_original(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    product_id: uuid.UUID,
    source: str,
    asset: Mapping[str, object],
) -> bool:
    with factory.begin() as session:
        product = session.scalar(
            select(PartnerProduct).where(PartnerProduct.id == product_id).with_for_update()
        )
        job = owned_job(session, ArticleImageJob, identifier, token)
        if job is None or product is None:
            return False
        if product.product_url == source and not (product.managed_image or {}).get("variants"):
            product.managed_image = dict(asset)
    return True


def store_logo(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    subject_id: SubjectIdentity,
    source: str,
    *,
    model: type[LogoSubject],
) -> None:
    from devfeed_core.logos import logo_sizes

    from devfeed_aggregator.logo_storage import LogoOriginalStore, LogoVariantStore

    original_store = LogoOriginalStore
    if model is PartnerProduct:
        from devfeed_aggregator.product_logo_storage import ProductLogoOriginalStore

        original_store = ProductLogoOriginalStore

    _store_asset(
        factory,
        identifier,
        token,
        subject_id,
        source,
        model=model,
        source_field="product_url" if model is PartnerProduct else "logo_url",
        asset_field="managed_logo",
        original_store=original_store,
        variant_store=lambda storage: LogoVariantStore(storage, sizes=logo_sizes(model)),
        method="r2-logo",
    )


def _replacement(
    session: Session,
    model: type[ImageSubject],
    subject: ImageSubject,
    *,
    asset_field: str = "managed_logo",
) -> None:
    # The selected model determines the subject contract; preserve model-based
    # dispatch even for compatible ORM adapters and detached test records.
    if model is Article:
        from devfeed_core.image_jobs import request_image

        session.flush()
        request_image(session, cast(Article, subject).id, automatic=True)
    elif model is PartnerProductPromotion:
        from devfeed_core.partner_product_images import request_product_ad_image

        session.flush()
        policy = cast(PartnerProductPromotion, subject)
        request_product_ad_image(session, policy.account_id, policy.product_id)
    elif model is PartnerProduct and asset_field == "managed_image":
        from devfeed_core.partner_product_images import request_product_image

        session.flush()
        request_product_image(session, cast(PartnerProduct, subject).id, automatic=True)
    elif model in {Topic, Source, PartnerProduct}:
        from devfeed_core.logos import request_logo

        session.flush()
        request_logo(session, model, cast(LogoSubject, subject).id, automatic=True)


def _store_asset(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    subject_id: SubjectIdentity,
    source: str,
    *,
    model: type[ImageSubject],
    source_field: str,
    asset_field: str,
    original_store: Callable[[R2Storage], OriginalStore],
    variant_store: Callable[[R2Storage], VariantStore],
    method: str,
) -> None:
    try:
        with factory() as session:
            job = session.get(ArticleImageJob, identifier)
            if job is None or job.status != "running" or job.lease_token != token:
                return
            # Resume a checkpoint emitted by these typed stores. Persistence
            # preserves this versioned schema; SQLAlchemy exposes a dynamic record.
            asset = cast(
                StoredImage,
                {
                    key: value
                    for key, value in (job.storage or {}).items()
                    if key not in {"article_refresh", "source_pipeline_request"}
                },
            )
        with storage_client() as client:
            original = original_store(client)
            variants = variant_store(client)
            imported = not asset
            if imported:
                asset = original.store(source)
                if not checkpoint(factory, identifier, token, asset):
                    return
            if asset_field == "managed_logo" and not publish_logo_original(
                factory,
                identifier,
                token,
                subject_id,
                source,
                asset,
                defer=imported,
                model=cast(type[LogoSubject], model),
            ):
                return
            if asset_field == "managed_logo" and imported:
                return  # Resume from R2 in a later delivery, after older original imports.
            if (
                model is PartnerProduct
                and asset_field == "managed_image"
                and not publish_product_image_original(
                    factory, identifier, token, cast(uuid.UUID, subject_id), source, asset
                )
            ):
                return
            for size in variants.sizes(asset):
                if any(item["width"] == size for item in asset["variants"]):
                    continue
                item = variants.store(asset, size)
                asset = {
                    **asset,
                    "variants": sorted([*asset["variants"], item], key=lambda item: item["width"]),
                }
                if not checkpoint(factory, identifier, token, asset):
                    return
        if model is Article and asset.get("thumbnail_version") == THUMBNAIL_VERSION:
            asset = {**asset, "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT}
        with factory.begin() as session:
            subject = _lock_subject(session, model, subject_id)
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None or subject is None:
                return
            current = _current_source(subject, job, source_field, source)
            if current:
                setattr(
                    subject,
                    asset_field,
                    {**asset, "subject_revision": job.subject_revision}
                    if model is PartnerProductPromotion
                    else dict(asset),
                )
            job.method = method
            marker = (job.storage or {}).get("source_pipeline_request")
            job.storage = {**asset, **({"source_pipeline_request": marker} if marker else {})}
            finish_job(job, "found" if current else "already_present", utcnow())
            if not current:
                _replacement(session, model, subject, asset_field=asset_field)
            elif model is Source:
                session.flush()
                from devfeed_core.source_lifecycle import reconcile_source_processing

                reconcile_source_processing(session, cast(Source, subject), automatic=True)
        logger.info("image_storage_completed", extra={"variant_count": len(asset["variants"])})
    except Exception as exc:
        transport = exc if isinstance(exc, FeedError) else None
        # Client exceptions may contain signed URLs; persist safe categories only.
        reason = transport.reason if transport else type(exc).__name__
        with factory.begin() as session:
            subject = _lock_subject(session, model, subject_id)
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None or subject is None:
                return
            if model in {
                Topic,
                Source,
                PartnerProduct,
                PartnerProductPromotion,
            } and not _current_source(subject, job, source_field, source):
                finish_job(job, "already_present", utcnow())
                _replacement(session, model, subject, asset_field=asset_field)
                return
            if (
                model is PartnerProduct
                and asset_field == "managed_image"
                and transport
                and transport.reason == "product_image_not_found"
            ):
                finish_job(job, "not_found", utcnow())
                logger.info("product_image_not_found")
                return
            job.http_status = transport.status if transport else None
            fail_or_retry(
                job,
                f"Image storage failed: {reason}",
                utcnow(),
                retryable=transport.retryable if transport else True,
                retry_after=transport.retry_after if transport else 0,
            )
        logger.warning("image_storage_failed", extra={"reason": reason})
