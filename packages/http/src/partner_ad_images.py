"""Private product image uploads share avatar storage and account-scoped placement policy."""

from contextlib import ExitStack
from urllib.parse import unquote, urlsplit

from devfeed_core.config import get_settings
from devfeed_core.managed_images import IMAGE_VERSION
from devfeed_core.models import PartnerProduct, PartnerProductPromotion
from devfeed_core.object_storage import R2Storage
from devfeed_core.partner_product_images import (
    AD_IMAGE_SIZES,
    ad_image_prefix,
    request_product_ad_image,
    uploaded_ad_image_prefix,
)
from devfeed_core.partner_promotions import associated_products_query
from devfeed_core.urls import validate_public_url
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from devfeed_http.image_uploads import MAX_BYTES, ImageUploadStorage
from devfeed_http.partner_portal import (
    _product_promotion_policy,
    product_promotion,
    save_product_promotion,
)


class ProductAdImageStorage(ImageUploadStorage):
    """Fixed artwork slots preserve aspect ratio for each owned product-image prefix."""

    sizes = AD_IMAGE_SIZES

    def __init__(self, prefix, *, storage: R2Storage | None = None):
        super().__init__(prefix, self.sizes, storage=storage)


def lock_image_product(session, account_id, product_id):
    if (
        session.execute(
            associated_products_query(account_id).where(PartnerProduct.id == product_id)
        ).first()
        is None
    ):
        raise HTTPException(404, "Product not found in this account's active resolved catalog")
    # Authorization is rechecked after acquiring product/account locks.
    product = session.scalar(
        select(PartnerProduct)
        .where(PartnerProduct.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if product is None or product.merged_into_id:
        raise HTTPException(404, "Product not found in this account's active resolved catalog")


def lock_image_account(session, query):
    try:
        return session.scalar(
            query.with_for_update(nowait=True).execution_options(populate_existing=True)
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":
            raise
        session.rollback()
        raise HTTPException(
            503,
            "Product settings are being updated; try again shortly",
            headers={"Retry-After": "1"},
        ) from None


def save_product_promotion_with_images(session, account, product_id, payload):
    previous = _product_promotion_policy(session, account, product_id, lock=True)
    prefix = uploaded_ad_image_prefix(previous.managed_ad_image, account.id)
    removing = (
        prefix
        and "ad_image_url" in payload.model_fields_set
        and payload.ad_image_url != previous.ad_image_url
    )
    if removing and payload.ad_image_url:
        current, incoming = urlsplit(previous.ad_image_url), urlsplit(payload.ad_image_url)
        directory = current.path.rsplit("/", 1)[0] + "/"
        if (incoming.scheme, incoming.netloc) == (current.scheme, current.netloc) and unquote(
            incoming.path
        ).startswith(directory):
            raise HTTPException(
                422, "Upload a new file or use another image URL instead of editing an uploaded URL"
            )
    with ExitStack() as stack:
        if removing:
            objects = stack.enter_context(ProductAdImageStorage(prefix))
            objects.remove()
        result = save_product_promotion(session, account, product_id, payload)
        session.commit()
        return result


def upload_product_ad_image(session, account, product_id, file):
    previous = _product_promotion_policy(session, account, product_id, lock=True)
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Choose a JPEG, PNG or WebP image")
    try:
        base = validate_public_url(get_settings().image_public_url or "")
        if not base.startswith("https://"):
            raise ValueError("HTTPS required")
    except ValueError:
        raise HTTPException(503, "Public HTTPS image storage is required for ad uploads") from None
    prefix = ad_image_prefix(account.id, product_id)
    upload = ProductAdImageStorage(prefix)
    encoded, widths = upload.normalize(file.file.read(MAX_BYTES + 1))
    old_prefix = uploaded_ad_image_prefix(previous.managed_ad_image, account.id)
    with ExitStack() as stack:
        if old_prefix and old_prefix != prefix:
            old = stack.enter_context(ProductAdImageStorage(old_prefix))
            old.remove()
        objects = stack.enter_context(upload)
        objects.replace(encoded, widths=widths)
        # Fixed slots stay bounded; actual widths are unique srcset descriptors.
        by_width = {
            widths[size]: {"key": f"{prefix}/{size}.webp", "width": widths[size]}
            for size in AD_IMAGE_SIZES
        }
        variants = [by_width[width] for width in sorted(by_width)]
        chosen = next((item for item in variants if item["width"] >= 640), variants[-1])
        source = f"{base.rstrip('/')}/{chosen['key']}?v={objects.revision}"
        revision = (previous.ad_image_revision or 0) + 1
        asset = {
            "upload": True,
            "object_prefix": prefix,
            "object_product_id": str(product_id),
            "cache_version": objects.revision,
            "source_url": source,
            "subject_revision": revision,
            "version": IMAGE_VERSION,
            "variants": variants,
        }
        values = dict(ad_image_url=source, managed_ad_image=asset, ad_image_revision=revision)
        session.execute(
            insert(PartnerProductPromotion)
            .values(account_id=account.id, product_id=product_id, **values)
            .on_conflict_do_update(
                index_elements=[
                    PartnerProductPromotion.account_id,
                    PartnerProductPromotion.product_id,
                ],
                set_=values,
            )
        )
        # A replaced URL job loses its lease before it can publish over this upload.
        request_product_ad_image(session, account.id, product_id)
        result = product_promotion(session, account, product_id)
        session.commit()
        return result
