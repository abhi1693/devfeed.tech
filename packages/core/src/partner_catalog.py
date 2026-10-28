"""Canonical product identity and independent launch platform provenance."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import cast
from urllib.parse import urlsplit

from sqlalchemy import exists, or_, select, text, update
from sqlalchemy.engine import ScalarResult
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from devfeed_core.job_lifecycle import fail_or_retry, finish_job
from devfeed_core.models import (
    ArticleImageJob,
    PartnerAccount,
    PartnerConnection,
    PartnerEvaluation,
    PartnerListing,
    PartnerManualProduct,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerProductURL,
    utcnow,
)
from devfeed_core.partner_link_rel import configured_link_rel
from devfeed_core.partner_tools import ListingData, ProductFacts, ProductInput, eligible
from devfeed_core.urls import canonicalize_url, fingerprint

logger = logging.getLogger(__name__)


def lock_catalog(session: Session) -> None:
    # Only short database mutations are serialized. Network/AI work runs outside this lock.
    # This also orders cross-provider merges, exclusions and job cancellation consistently.
    session.execute(text("SELECT pg_advisory_xact_lock(734819025)"))


def active_provider(session: Session, product_id: uuid.UUID) -> str | None:
    return session.scalar(
        select(PartnerListing.provider)
        .join(PartnerConnection, PartnerConnection.provider == PartnerListing.provider)
        .where(
            PartnerListing.product_id == product_id,
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
            PartnerConnection.enabled.is_(True),
            PartnerConnection.archived_at.is_(None),
        )
        .order_by(PartnerListing.provider)
        .limit(1)
    )


def has_listings() -> ColumnElement[bool]:
    return exists(select(PartnerListing.id).where(PartnerListing.product_id == PartnerProduct.id))


def has_active_listing() -> ColumnElement[bool]:
    return exists(
        select(PartnerListing.id)
        .join(PartnerConnection, PartnerConnection.provider == PartnerListing.provider)
        .where(
            PartnerListing.product_id == PartnerProduct.id,
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
            PartnerConnection.enabled.is_(True),
            PartnerConnection.archived_at.is_(None),
        )
    )


def has_sources() -> ColumnElement[bool]:
    return or_(
        has_listings(),
        exists(
            select(PartnerManualProduct.product_id).where(
                PartnerManualProduct.product_id == PartnerProduct.id
            )
        ),
    )


def has_active_source() -> ColumnElement[bool]:
    """Qualification and image preparation work without any import connection."""
    return or_(
        has_active_listing(),
        exists(
            select(PartnerManualProduct.product_id).where(
                PartnerManualProduct.product_id == PartnerProduct.id,
                PartnerManualProduct.active.is_(True),
            )
        ),
    )


def product_has_active_source(session: Session, product_id: uuid.UUID | None) -> bool:
    return bool(
        session.scalar(
            select(PartnerProduct.id).where(PartnerProduct.id == product_id, has_active_source())
        )
    )


def listing_views(
    session: Session, product_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[ListingData]]:
    result: dict[uuid.UUID, list[ListingData]] = {identifier: [] for identifier in product_ids}
    if not result:
        return result
    for listing, enabled, name, account, promotion, product in session.execute(
        select(
            PartnerListing,
            PartnerConnection.enabled,
            PartnerConnection.name,
            PartnerAccount,
            PartnerProductPromotion,
            PartnerProduct,
        )
        .join(PartnerConnection, PartnerConnection.provider == PartnerListing.provider)
        .outerjoin(PartnerAccount, PartnerAccount.id == PartnerConnection.account_id)
        .outerjoin(
            PartnerProductPromotion,
            (PartnerProductPromotion.account_id == PartnerAccount.id)
            & (PartnerProductPromotion.product_id == PartnerListing.product_id),
        )
        .join(PartnerProduct, PartnerProduct.id == PartnerListing.product_id)
        .where(PartnerListing.product_id.in_(product_ids))
        .order_by(PartnerListing.provider, PartnerListing.external_id)
    ):
        trusted = bool(
            account
            and account.approval_mode == "trusted"
            and promotion
            and promotion.commercial_approved_at
            and promotion.approval_source == "trusted"
        )
        accepted = bool(
            account
            and account.status == "active"
            and enabled
            and listing.active
            and listing.identity_status == "resolved"
            and not product.excluded
            and not product.merged_into_id
            and not (promotion and promotion.excluded)
            and (eligible(product) or trusted)
        )
        result[listing.product_id].append(
            cast(
                ListingData,
                {
                    **{
                        field: getattr(listing, field)
                        for field in (
                            "id",
                            "provider",
                            "external_id",
                            "name",
                            "product_url",
                            "listing_url",
                            "tagline",
                            "pricing",
                            "attribution",
                            "active",
                            "identity_status",
                            "identity_reason",
                            "updated_at",
                        )
                    },
                    "platform_name": name or listing.provider.replace("-", " ").title(),
                    "connection_enabled": enabled,
                    "account_id": account.id if account else None,
                    "account_name": account.name if account else None,
                    "commercial_approved": accepted,
                    "approval_source": "trusted"
                    if trusted
                    else "qualified"
                    if eligible(product)
                    else None,
                },
            )
        )
    return result


def refresh_availability(session: Session, product: PartnerProduct) -> None:
    if product.merged_into_id:
        return
    available = session.scalar(
        select(PartnerListing.id)
        .where(
            PartnerListing.product_id == product.id,
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
        .limit(1)
    )
    available = available or session.scalar(
        select(PartnerManualProduct.product_id)
        .where(PartnerManualProduct.product_id == product.id, PartnerManualProduct.active.is_(True))
        .limit(1)
    )
    if not available and product.status != "withdrawn":
        product.status, product.verified_at = "withdrawn", None
        product.revision += 1
        product.assessment = {
            "state": "withdrawn",
            "reason": "No active product sources remain.",
        }
    elif available and product.status == "withdrawn":
        product.status = "paused" if product.excluded else "pending"
        product.revision += 1
        product.assessment_revision = 0


def upsert_listing(session: Session, item: ProductInput, generation: uuid.UUID) -> PartnerProduct:
    from devfeed_core.partner_connections import queue_evaluation, request_assessment

    lock_catalog(session)
    url = canonicalize_url(item.product_url)
    key = fingerprint(url)
    alias = session.get(PartnerProductURL, key)
    listing = session.scalar(
        select(PartnerListing).where(
            PartnerListing.provider == item.provider, PartnerListing.external_id == item.external_id
        )
    )
    if listing:
        product = session.get(PartnerProduct, listing.product_id)
        assert product is not None
        # Changed destinations need a verified alias, not a fresh product that evades exclusions.
        resolved = bool(alias and alias.product_id == product.id)
    elif alias:
        product = session.get(PartnerProduct, alias.product_id)
        assert product is not None
        resolved = True
    else:
        product = PartnerProduct(
            **ProductFacts.model_validate(
                {k: v for k, v in item.model_dump().items() if k in ProductFacts.model_fields}
            ).model_dump(exclude={"technologies", "evidence"}),
            technologies=[],
            evidence=[],
            reviews=[],
        )
        product.product_url = url
        session.add(product)
        session.flush()
        session.add(PartnerProductURL(url_hash=key, url=url, product_id=product.id))
        resolved = True
    source = item.model_dump(exclude={"technologies", "evidence"})
    if listing is None:
        listing = PartnerListing(**source, product_id=product.id)
        session.add(listing)
        session.flush()
    else:
        for field, value in source.items():
            setattr(listing, field, value)
    listing.active, listing.seen_generation, listing.updated_at = True, generation, utcnow()
    listing.identity_status = "resolved" if resolved else "unresolved"
    listing.identity_reason = (
        None
        if resolved
        else "The platform changed the product website; waiting for a verified URL alias."
    )
    previous_revision = product.revision
    if product.metadata_listing_id is None and product.metadata_account_id is None:
        product.metadata_listing_id = listing.id
    if resolved and product.metadata_listing_id == listing.id:
        # Display metadata has a stable source. Other platforms cannot overwrite it.
        changes = {field: getattr(item, field) for field in ("name", "tagline", "pricing")}
        if any(getattr(product, field) != value for field, value in changes.items()):
            for field, value in changes.items():
                setattr(product, field, value)
            product.revision += 1
            product.verified_at = None
            product.technologies, product.evidence = [], []
            product.assessment = {}
    session.flush()
    refresh_availability(session, product)
    # Listing timestamps track imports; unchanged products keep their prepared matches.
    if product.revision != previous_revision:
        product.updated_at = utcnow()
    if (
        not product.excluded
        and product_has_active_source(session, product.id)
        and (
            product.assessment_revision != product.revision
            or (product.status == "approved" and not eligible(product))
        )
    ):
        request_assessment(session, product)
    queue_evaluation(session, product)
    from .partner_promotions import ensure_automatic_assets

    ensure_automatic_assets(session, product_ids=[product.id])
    return product


def withdraw_missing_listings(
    session: Session, provider: str, generation: uuid.UUID, *, limit: int | None = None
) -> int:
    missing = session.scalars(
        select(PartnerListing)
        .where(
            PartnerListing.provider == provider,
            PartnerListing.active.is_(True),
            (PartnerListing.seen_generation != generation)
            | PartnerListing.seen_generation.is_(None),
        )
        .order_by(PartnerListing.id)
        .limit(limit)
    ).all()
    products = set()
    for listing in missing:
        listing.active = False
        listing.updated_at = utcnow()
        products.add(listing.product_id)
    session.flush()
    for identifier in sorted(products):
        product = session.get(PartnerProduct, identifier)
        assert product is not None
        refresh_availability(session, product)
    return len(missing)


def record_verified_redirect(
    session: Session, product: PartnerProduct, final_url: str
) -> PartnerProduct:
    """Only observed scheme/www redirects with identical resource paths establish aliases.

    Cross-domain redirects or path changes may lead to login/parking/umbrella pages, so
    they remain insufficient identity evidence. Exact URLs still work independently.
    """
    source = urlsplit(canonicalize_url(product.product_url))
    target_url = canonicalize_url(final_url)
    target = urlsplit(target_url)
    if (
        (source.hostname or "").removeprefix("www.") != (target.hostname or "").removeprefix("www.")
        or source.path != target.path
        or source.query != target.query
    ):
        return product
    if source.scheme == "https" and target.scheme != "https":
        return product
    alias = session.get(PartnerProductURL, fingerprint(target_url))
    if alias and alias.product_id != product.id:
        recipient = session.get(PartnerProduct, alias.product_id)
        assert recipient is not None
        return merge_products(session, product, recipient)
    if not alias:
        alias = PartnerProductURL(
            url_hash=fingerprint(target_url), url=target_url, product_id=product.id
        )
        session.add(alias)
    alias.verified_at = utcnow()
    # A previously unresolved listing can now be linked by this verified URL.
    for listing in session.scalars(
        select(PartnerListing).where(
            PartnerListing.product_id == product.id, PartnerListing.identity_status == "unresolved"
        )
    ):
        if canonicalize_url(listing.product_url) == target_url:
            listing.identity_status, listing.identity_reason = "resolved", None
    return product


def _active_merge_jobs[T: PartnerPipelineJob | PartnerEvaluation](
    session: Session,
    model: type[T],
    identifiers: list[uuid.UUID],
) -> ScalarResult[T]:
    return session.scalars(
        select(model).where(
            model.product_id.in_(identifiers),
            model.status.in_(["queued", "running"]),
        )
    )


def merge_products(
    session: Session, donor: PartnerProduct, recipient: PartnerProduct
) -> PartnerProduct:
    """Preserve provenance, exclusions and valid qualification when merging aliases."""
    from devfeed_core.partner_connections import request_assessment

    if donor.id == recipient.id:
        return recipient
    excluded_before = recipient.excluded
    recipient.excluded = recipient.excluded or donor.excluded
    recipient.reviews = [*recipient.reviews, *donor.reviews]
    if recipient.excluded and not excluded_before:
        recipient.revision += 1
        recipient.verified_at = None
        recipient.assessment_revision = 0
        recipient.status = "paused"
    cancelled_products = [donor.id, recipient.id] if recipient.excluded else [donor.id]
    donor.merged_into_id, donor.status, donor.verified_at = recipient.id, "withdrawn", None
    donor.revision += 1
    job_models: tuple[type[PartnerPipelineJob] | type[PartnerEvaluation], ...] = (
        PartnerPipelineJob,
        PartnerEvaluation,
    )
    for model in job_models:
        for job in _active_merge_jobs(session, model, cancelled_products):
            fail_or_retry(
                job,
                "Product identities merged; use the canonical product job",
                utcnow(),
                retryable=False,
            )
    session.execute(
        update(PartnerListing)
        .where(PartnerListing.product_id == donor.id)
        .values(product_id=recipient.id)
    )
    session.execute(
        update(PartnerProductURL)
        .where(PartnerProductURL.product_id == donor.id)
        .values(product_id=recipient.id)
    )
    # Preserve explicit inventory when a website redirects to a known canonical URL.
    # The recipient keeps its metadata authority; redirect aliases grant no edit rights.
    for source in list(
        session.scalars(
            select(PartnerManualProduct).where(PartnerManualProduct.product_id == donor.id)
        )
    ):
        current_manual = session.get(PartnerManualProduct, (source.account_id, recipient.id))
        if current_manual is None:
            session.add(
                PartnerManualProduct(
                    account_id=source.account_id,
                    product_id=recipient.id,
                    active=source.active,
                    created_at=source.created_at,
                    updated_at=source.updated_at,
                )
            )
        # An existing archived destination is a deliberate choice and wins.
        source.active = False
    # Identity consolidation must not erase an account's product restrictions.
    # Keep donor policy for historical receipts and conservatively combine the
    # current canonical policy; null inherits, an empty allowlist blocks all.
    donor_policies = list(
        session.scalars(
            select(PartnerProductPromotion)
            .where(PartnerProductPromotion.product_id == donor.id)
            .order_by(PartnerProductPromotion.account_id)
        )
    )
    transferred_images, discarded_images = [], []
    for policy in donor_policies:
        account = session.scalar(
            select(PartnerAccount).where(PartnerAccount.id == policy.account_id).with_for_update()
        )
        assert account is not None
        # The account may have been edited while this merge waited for its lock.
        session.refresh(policy)
        current = session.get(PartnerProductPromotion, (policy.account_id, recipient.id))
        if current is None:
            current = PartnerProductPromotion(
                account_id=policy.account_id, product_id=recipient.id, excluded=False
            )
            session.add(current)
        # An explicit canonical choice (including clearing) wins. A never-customized
        # canonical policy may inherit this account's image from its merged alias.
        if not current.ad_image_url and not current.ad_image_revision and policy.ad_image_url:
            current.ad_image_url = policy.ad_image_url
            current.ad_image_revision = max(policy.ad_image_revision, 1)
            current.managed_ad_image = {
                **(policy.managed_ad_image or {}),
                "subject_revision": current.ad_image_revision,
            }
            transferred_images.append((policy.account_id, recipient.id))
        elif policy.managed_ad_image and policy.managed_ad_image.get("upload"):
            discarded_images.append(policy)
        current.excluded = bool(current.excluded or policy.excluded)
        # Merge relationship requirements conservatively: neither alias may
        # remove a nofollow (or other relationship) configured on the other.
        if current.link_rel is not None or policy.link_rel is not None:
            current.link_rel = sorted(
                set(configured_link_rel(account, current))
                | set(configured_link_rel(account, policy))
            )
        for field in ("ad_formats", "promotion_surfaces"):
            previous, incoming = getattr(current, field), getattr(policy, field)
            if incoming is not None:
                setattr(
                    current,
                    field,
                    sorted(set(incoming) if previous is None else set(previous) & set(incoming)),
                )
        if current.commercial_approved_at is None and policy.commercial_approved_at is not None:
            current.commercial_approved_at = policy.commercial_approved_at
            current.approval_source = policy.approval_source
    session.flush()
    for image_job in session.scalars(
        select(ArticleImageJob)
        .where(
            ArticleImageJob.product_id == donor.id,
            ArticleImageJob.operation == "product-ad-image",
            ArticleImageJob.status.in_(["queued", "running"]),
        )
        .with_for_update()
    ):
        finish_job(image_job, "already_present", utcnow())
    if discarded_images:
        from devfeed_core.partner_product_images import request_ad_image_cleanup

        session.flush()
        for policy in discarded_images:
            request_ad_image_cleanup(session, policy)
    if transferred_images:
        from devfeed_core.partner_product_images import request_product_ad_image

        for account_id, product_id in transferred_images:
            request_product_ad_image(session, account_id, product_id)
    refresh_availability(session, recipient)
    if not eligible(recipient) and not recipient.excluded:
        request_assessment(session, recipient)
    logger.info(
        "partner_products_merged",
        extra={
            "product_id": str(recipient.id),
            "previous_product_id": str(donor.id),
            "excluded": recipient.excluded,
        },
    )
    return recipient
