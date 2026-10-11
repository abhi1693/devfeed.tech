"""Product preview-image outbox and managed presentation; readers never fetch upstreams."""

import uuid

from sqlalchemy import or_, select

from devfeed_core.config import get_settings
from devfeed_core.managed_images import IMAGE_VERSION, THUMBNAIL_VERSION
from devfeed_core.models import ArticleImageJob, PartnerProduct
from devfeed_core.partner_catalog import has_active_listing
from devfeed_core.services import RecordNotFound


def product_image_variants(product) -> list[dict]:
    settings = get_settings()
    asset = getattr(product, "managed_image", None) or {}
    if (
        not settings.image_storage_enabled
        or not settings.image_public_url
        or asset.get("page_url") != product.product_url
        or asset.get("version") != IMAGE_VERSION
    ):
        return []
    return [
        {"url": f"{settings.image_public_url}/{item['key']}", "width": item["width"]}
        for item in asset.get("variants", [])
    ]


def product_image_url(product) -> str | None:
    variants = product_image_variants(product)
    if variants:
        return next((item["url"] for item in variants if item["width"] >= 640), variants[-1]["url"])
    settings = get_settings()
    asset = getattr(product, "managed_image", None) or {}
    if (
        settings.image_storage_enabled
        and settings.image_public_url
        and asset.get("page_url") == product.product_url
        and asset.get("version") == IMAGE_VERSION
        and asset.get("original_key")
    ):
        return f"{settings.image_public_url}/{asset['original_key']}"
    return None


def product_image_current(product) -> bool:
    asset = product.managed_image or {}
    return bool(
        asset.get("page_url") == product.product_url
        and asset.get("version") == IMAGE_VERSION
        and asset.get("variants")
    )


def request_product_image(session, product_id: uuid.UUID, *, automatic=False):
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


def backfill_product_images(session, limit=100):
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
            has_active_listing(),
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
