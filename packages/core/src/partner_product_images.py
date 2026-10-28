"""Product preview-image outbox and managed presentation; readers never fetch upstreams."""

import hashlib
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.json_types import JsonRecord
from devfeed_core.managed_images import IMAGE_VERSION, THUMBNAIL_VERSION, ImageVariant
from devfeed_core.models import ArticleImageJob, PartnerProduct, PartnerProductPromotion, utcnow
from devfeed_core.partner_catalog import has_active_source
from devfeed_core.services import RecordNotFound

AD_IMAGE_SIZES = (400, 800, 1200)


def ad_image_prefix(account_id: uuid.UUID, product_id: uuid.UUID) -> str:
    digest = hashlib.sha256(f"{account_id}:{product_id}".encode()).hexdigest()
    return f"partner-ad-images/{digest}"


def uploaded_ad_image_prefix(asset: JsonRecord | None, account_id: uuid.UUID) -> str | None:
    asset = asset or {}
    try:
        origin = uuid.UUID(asset.get("object_product_id", ""))
    except (ValueError, TypeError, AttributeError):
        return None
    prefix = asset.get("object_prefix", "")
    if not isinstance(prefix, str):
        return None
    return prefix if asset.get("upload") and prefix == ad_image_prefix(account_id, origin) else None


def _managed_url(asset: JsonRecord, key: str) -> str:
    suffix = f"?v={asset['cache_version']}" if asset.get("upload") else ""
    return f"{(get_settings().image_public_url or '').rstrip('/')}/{key}{suffix}"


def product_ad_image_current(policy: PartnerProductPromotion | None) -> bool:
    if policy is None or not policy.ad_image_url:
        return False
    asset = policy.managed_ad_image or {}
    return bool(
        asset.get("source_url") == policy.ad_image_url
        and asset.get("subject_revision") == policy.ad_image_revision
        and asset.get("version") == IMAGE_VERSION
        and asset.get("variants")
    )


def _presentation_asset(
    product: PartnerProduct, policy: PartnerProductPromotion | None = None
) -> JsonRecord:
    settings = get_settings()
    if not settings.image_storage_enabled or not settings.image_public_url:
        return {}
    if product_ad_image_current(policy):
        assert policy is not None
        return policy.managed_ad_image
    asset = getattr(product, "managed_image", None) or {}
    if asset.get("page_url") != product.product_url or asset.get("version") != IMAGE_VERSION:
        return {}
    return asset


def product_image_variants(
    product: PartnerProduct, policy: PartnerProductPromotion | None = None
) -> list[ImageVariant]:
    asset = _presentation_asset(product, policy)
    return [
        {"url": _managed_url(asset, item["key"]), "width": item["width"]}
        for item in asset.get("variants", [])
    ]


def product_image_url(
    product: PartnerProduct, policy: PartnerProductPromotion | None = None
) -> str | None:
    variants = product_image_variants(product, policy)
    if variants:
        return next((item["url"] for item in variants if item["width"] >= 640), variants[-1]["url"])
    asset = _presentation_asset(product, policy)
    if asset.get("original_key"):
        return _managed_url(asset, asset["original_key"])
    return None


def request_product_ad_image(
    session: Session, account_id: uuid.UUID, product_id: uuid.UUID
) -> ArticleImageJob | None:
    """Coalesce one account's durable custom-image work; replacement invalidates its lease."""
    product = session.scalar(
        select(PartnerProduct).where(PartnerProduct.id == product_id).with_for_update()
    )
    if product is None:
        raise RecordNotFound("Product not found")
    policy = session.scalar(
        select(PartnerProductPromotion)
        .where(
            PartnerProductPromotion.account_id == account_id,
            PartnerProductPromotion.product_id == product_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    active = session.scalar(
        select(ArticleImageJob)
        .where(
            ArticleImageJob.account_id == account_id,
            ArticleImageJob.product_id == product_id,
            ArticleImageJob.operation == "product-ad-image",
            ArticleImageJob.status.in_(["queued", "running"]),
        )
        .with_for_update()
    )
    wanted = bool(policy and policy.ad_image_url and not product.merged_into_id)
    if active is not None:
        if (
            wanted
            and policy is not None
            and active.image_url == policy.ad_image_url
            and active.subject_revision == policy.ad_image_revision
        ):
            return active
        finish_job(active, "already_present", utcnow())
        session.flush()
    if not wanted or not get_settings().image_storage_enabled or product_ad_image_current(policy):
        return None
    assert policy is not None
    job = ArticleImageJob(
        account_id=account_id,
        product_id=product_id,
        operation="product-ad-image",
        image_url=policy.ad_image_url,
        subject_revision=policy.ad_image_revision,
        storage_version=THUMBNAIL_VERSION,
    )
    session.add(job)
    session.flush()
    return job


def request_ad_image_cleanup(
    session: Session, policy: PartnerProductPromotion
) -> ArticleImageJob | None:
    prefix = uploaded_ad_image_prefix(policy.managed_ad_image, policy.account_id)
    if not prefix:
        return None
    active = session.scalar(
        select(ArticleImageJob).where(
            ArticleImageJob.account_id == policy.account_id,
            ArticleImageJob.product_id == policy.product_id,
            ArticleImageJob.operation == "product-ad-image",
            ArticleImageJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        return active
    job = ArticleImageJob(
        account_id=policy.account_id,
        product_id=policy.product_id,
        subject_revision=policy.ad_image_revision,
        operation="product-ad-image",
        image_url=policy.ad_image_url,
        storage_version=THUMBNAIL_VERSION,
        storage={"cleanup_upload": prefix},
    )
    session.add(job)
    session.flush()
    return job


def product_ad_image_status(session: Session, policy: PartnerProductPromotion | None) -> str:
    if not policy or not policy.ad_image_url:
        return "default"
    if product_ad_image_current(policy):
        return "ready"
    status = session.scalar(
        select(ArticleImageJob.status)
        .where(
            ArticleImageJob.account_id == policy.account_id,
            ArticleImageJob.product_id == policy.product_id,
            ArticleImageJob.operation == "product-ad-image",
            ArticleImageJob.subject_revision == policy.ad_image_revision,
        )
        .order_by(ArticleImageJob.created_at.desc(), ArticleImageJob.id.desc())
        .limit(1)
    )
    return status if status in {"queued", "running"} else "failed"


def product_image_current(product: PartnerProduct) -> bool:
    asset = product.managed_image or {}
    return bool(
        asset.get("page_url") == product.product_url
        and asset.get("version") == IMAGE_VERSION
        and asset.get("variants")
    )


def request_product_image(
    session: Session, product_id: uuid.UUID, *, automatic: bool = False
) -> ArticleImageJob | None:
    if not get_settings().image_storage_enabled:
        return None
    product = session.scalar(
        select(PartnerProduct).where(PartnerProduct.id == product_id).with_for_update()
    )
    if product is None:
        raise RecordNotFound("Product not found")
    if (
        not product.product_url
        or product.excluded
        or product.merged_into_id
        or product_image_current(product)
    ):
        return None
    active = session.scalar(
        select(ArticleImageJob).where(
            ArticleImageJob.product_id == product.id,
            ArticleImageJob.operation == "product-image",
            ArticleImageJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        return active
    if automatic and session.scalar(
        select(ArticleImageJob.id)
        .where(
            ArticleImageJob.product_id == product.id,
            ArticleImageJob.operation == "product-image",
            ArticleImageJob.image_url == product.product_url,
            ArticleImageJob.storage_version == THUMBNAIL_VERSION,
        )
        .limit(1)
    ):
        return None
    job = ArticleImageJob(
        product_id=product.id,
        operation="product-image",
        image_url=product.product_url,
        storage_version=THUMBNAIL_VERSION,
    )
    session.add(job)
    session.flush()
    return job


def backfill_product_images(session: Session, limit: int = 100) -> list[ArticleImageJob]:
    """Explicit bounded backfill for approved imports, including trusted account acceptance."""
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    if not get_settings().image_storage_enabled:
        return []
    from devfeed_core.partner_promotions import trusted_product_exists

    attempted = select(ArticleImageJob.id).where(
        ArticleImageJob.product_id == PartnerProduct.id,
        ArticleImageJob.operation == "product-image",
        or_(
            ArticleImageJob.status.in_(["queued", "running"]),
            (ArticleImageJob.image_url == PartnerProduct.product_url)
            & (ArticleImageJob.storage_version == THUMBNAIL_VERSION),
        ),
    )
    identifiers = session.scalars(
        select(PartnerProduct.id)
        .where(
            or_(PartnerProduct.status == "approved", trusted_product_exists()),
            PartnerProduct.excluded.is_(False),
            PartnerProduct.merged_into_id.is_(None),
            PartnerProduct.product_url.is_not(None),
            has_active_source(),
            ~attempted.exists(),
        )
        .order_by(PartnerProduct.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    return [
        job
        for identifier in identifiers
        if (job := request_product_image(session, identifier, automatic=True)) is not None
    ]
