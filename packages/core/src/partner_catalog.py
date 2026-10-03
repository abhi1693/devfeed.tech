"""Canonical product identity and independent launch platform provenance."""

import logging
import uuid
from urllib.parse import urlsplit

from sqlalchemy import exists, select, text, update

from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.models import (
    PartnerConnection,
    PartnerEvaluation,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductURL,
    utcnow,
)
from devfeed_core.partner_providers import SUPPORTED_PARTNERS
from devfeed_core.partner_tools import ProductFacts, eligible
from devfeed_core.urls import canonicalize_url, fingerprint

logger = logging.getLogger(__name__)


def lock_catalog(session):
    # Only short database mutations are serialized. Network/AI work runs outside this lock.
    # This also orders cross-provider merges, exclusions and job cancellation consistently.
    session.execute(text("SELECT pg_advisory_xact_lock(734819025)"))


def active_provider(session, product_id):
    return session.scalar(
        select(PartnerListing.provider)
        .join(PartnerConnection, PartnerConnection.provider == PartnerListing.provider)
        .where(
            PartnerListing.product_id == product_id,
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
            PartnerConnection.enabled.is_(True),
        )
        .order_by(PartnerListing.provider)
        .limit(1)
    )


def has_listings():
    return exists(select(PartnerListing.id).where(PartnerListing.product_id == PartnerProduct.id))


def has_active_listing():
    return exists(
        select(PartnerListing.id)
        .join(PartnerConnection, PartnerConnection.provider == PartnerListing.provider)
        .where(
            PartnerListing.product_id == PartnerProduct.id,
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
            PartnerConnection.enabled.is_(True),
        )
    )


def listing_views(session, product_ids):
    result: dict[uuid.UUID, list[dict]] = {identifier: [] for identifier in product_ids}
    if not result:
        return result
    for listing, enabled in session.execute(
        select(PartnerListing, PartnerConnection.enabled)
        .join(PartnerConnection, PartnerConnection.provider == PartnerListing.provider)
        .where(PartnerListing.product_id.in_(product_ids))
        .order_by(PartnerListing.provider, PartnerListing.external_id)
    ):
        result[listing.product_id].append(
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
                        "description",
                        "pricing",
                        "attribution",
                        "active",
                        "identity_status",
                        "identity_reason",
                        "updated_at",
                    )
                },
                "platform_name": SUPPORTED_PARTNERS[listing.provider].name
                if listing.provider in SUPPORTED_PARTNERS
                else listing.provider.replace("-", " ").title(),
                "connection_enabled": enabled,
            }
        )
    return result


def refresh_availability(session, product):
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
    if not available and product.status != "withdrawn":
        product.status, product.verified_at = "withdrawn", None
        product.revision += 1
        product.assessment = {
            "state": "withdrawn",
            "reason": "No active, resolved platform listings remain.",
        }
    elif available and product.status == "withdrawn":
        product.status = "paused" if product.excluded else "pending"
        product.revision += 1
        product.assessment_revision = 0


def upsert_listing(session, item, generation):
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
    if product.metadata_listing_id is None:
        product.metadata_listing_id = listing.id
    if resolved and product.metadata_listing_id == listing.id:
        # Display metadata has a stable source. Other platforms cannot overwrite it.
        changes = {field: getattr(item, field) for field in ("name", "description", "pricing")}
        if any(getattr(product, field) != value for field, value in changes.items()):
            for field, value in changes.items():
                setattr(product, field, value)
            product.revision += 1
            product.verified_at = None
            product.technologies, product.evidence = [], []
            product.assessment = {}
    session.flush()
    refresh_availability(session, product)
    product.updated_at = utcnow()
    if (
        not product.excluded
        and active_provider(session, product.id)
        and (
            product.assessment_revision != product.revision
            or (product.status == "approved" and not eligible(product))
        )
    ):
        request_assessment(session, product)
    queue_evaluation(session, product)
    return product


def withdraw_missing_listings(session, provider, generation, *, limit=None):
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
        refresh_availability(session, session.get(PartnerProduct, identifier))
    return len(missing)


def record_verified_redirect(session, product, final_url):
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
        return merge_products(session, product, session.get(PartnerProduct, alias.product_id))
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


def merge_products(session, donor, recipient):
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
    for model in (PartnerPipelineJob, PartnerEvaluation):
        for job in session.scalars(
            select(model).where(
                model.product_id.in_(cancelled_products),
                model.status.in_(["queued", "running"]),
            )
        ):
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
    session.flush()
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
