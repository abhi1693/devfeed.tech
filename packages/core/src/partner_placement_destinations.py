"""Shared destination policy for batch preflight and locked placement issuance."""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select, tuple_

from devfeed_core.models import (
    PartnerConnection,
    PartnerListing,
    PartnerManualProduct,
    PartnerProduct,
)
from devfeed_core.partner_connection_logos import PlacementPlatform, connection_platform
from devfeed_core.urls import validate_public_url

LISTING_LIMIT = 20
LISTING_ORDER = (PartnerListing.provider, PartnerListing.external_id, PartnerListing.id)


def owned_listings(*conditions):
    return (
        select(PartnerListing)
        .join(PartnerConnection)
        .where(
            PartnerConnection.enabled.is_(True),
            PartnerConnection.archived_at.is_(None),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
            *conditions,
        )
    )


def manual_destinations(*conditions):
    return (
        select(PartnerProduct.product_url)
        .join(PartnerManualProduct, PartnerManualProduct.product_id == PartnerProduct.id)
        .where(
            PartnerProduct.merged_into_id.is_(None),
            PartnerManualProduct.active.is_(True),
            *conditions,
        )
    )


def account_listings(account_id, product_id, *, lock=False):
    query = (
        owned_listings(
            PartnerListing.product_id == product_id,
            PartnerConnection.account_id == account_id,
        )
        .order_by(*LISTING_ORDER)
        .limit(LISTING_LIMIT)
    )
    if lock:
        query = query.with_for_update(
            read=True, of=[PartnerListing, PartnerConnection], skip_locked=True
        )
    return query


@dataclass(frozen=True)
class AccountProductCreative:
    tagline: str
    listing_url: str | None
    platform: PlacementPlatform | None = None
    attribution: str | None = None

    def destination(self, account, product):
        if account.outbound_destination == "listing_url":
            return self.listing_url
        return devfeed_outbound_url(product.product_url)


def account_product_creative(session, account, product, *, lock=False):
    """Keep advertiser copy, attribution and destination on the same owned source.

    Canonical facts qualify relevance; an advertiser's live import supplies its
    creative. Scalar reads stay fresh even if this session loaded a listing before
    an import or edit. Issuance locks that source through the delivery transaction.
    """
    selected = None
    listing_url = None
    for row in session.execute(
        account_listings(account.id, product.id, lock=lock).with_only_columns(
            PartnerListing.tagline,
            PartnerListing.attribution,
            PartnerListing.listing_url,
            PartnerConnection.provider,
            PartnerConnection.name,
            PartnerConnection.logo_url,
            PartnerConnection.managed_logo,
            PartnerConnection.connector["attribution"].as_string().label("connection_attribution"),
        )
    ):
        selected = selected or row
        if url := devfeed_outbound_url(row.listing_url):
            listing_url = url
            selected = row
            break
    if listing_url is None:
        manual = manual_destinations(
            PartnerProduct.id == product.id,
            PartnerManualProduct.account_id == account.id,
        ).with_only_columns(PartnerProduct.tagline, PartnerProduct.product_url)
        if lock:
            manual = manual.with_for_update(read=True, of=PartnerManualProduct, skip_locked=True)
        direct = session.execute(manual).first()
        if direct is not None:
            listing_url = devfeed_outbound_url(direct.product_url)
            return AccountProductCreative(direct.tagline, listing_url)
    return (
        AccountProductCreative(
            selected.tagline,
            listing_url,
            connection_platform(selected),
            (
                selected.connection_attribution
                if selected.connection_attribution is not None
                else selected.attribution
            ).strip()
            or None,
        )
        if selected is not None
        else None
    )


def devfeed_outbound_url(destination):
    """HTTPS destination with DevFeed source, preserving other parameters and fragments."""
    try:
        fragment = urlsplit(destination).fragment
        destination = validate_public_url(destination)
    except (ValueError, TypeError, AttributeError):
        return None
    parts = urlsplit(destination)
    if parts.scheme != "https":
        return None
    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "utm_source"
    ]
    params.append(("utm_source", "devfeed"))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), fragment))
    return url if len(url) <= 2048 else None


class PlacementDestinations:
    """Reject unavailable destinations in bounded batches without attribution locks."""

    def __init__(self, session):
        self.session = session
        self._unavailable = set()

    def unavailable(self, account_id, product_id):
        return (account_id, product_id) in self._unavailable

    def reject(self, account_id, product_id):
        self._unavailable.add((account_id, product_id))

    def eligible_rows(self, rows):
        available, listings = set(), set()
        for asset, account, product, *_ in rows:
            if asset.ad_format == "sponsored_content":
                continue
            pair = (account.id, product.id)
            if pair in self._unavailable:
                continue
            if account.outbound_destination == "listing_url":
                listings.add(pair)
            elif devfeed_outbound_url(product.product_url) is not None:
                available.add(pair)
            else:
                self._unavailable.add(pair)
        if listings:
            available.update(self._available_listings(listings))
        return [
            row
            for row in rows
            if row[0].ad_format == "sponsored_content" or (row[1].id, row[2].id) in available
        ]

    def _available_listings(self, pairs):
        # Limit each exact account/product pair before URL validation, just as
        # the final locked resolver does. Scalar columns cannot seed stale ORM
        # instances that would mask a later listing or connection edit.
        ranked = (
            owned_listings(
                tuple_(PartnerConnection.account_id, PartnerListing.product_id).in_(pairs)
            )
            .with_only_columns(
                PartnerConnection.account_id,
                PartnerListing.product_id,
                PartnerListing.listing_url,
                func.row_number()
                .over(
                    partition_by=(PartnerConnection.account_id, PartnerListing.product_id),
                    order_by=LISTING_ORDER,
                )
                .label("rank"),
            )
            .subquery()
        )
        available = {
            (account_id, product_id)
            for account_id, product_id, url in self.session.execute(
                select(ranked.c.account_id, ranked.c.product_id, ranked.c.listing_url).where(
                    ranked.c.rank <= LISTING_LIMIT
                )
            )
            if devfeed_outbound_url(url) is not None
        }
        missing = pairs - available
        if missing:
            available.update(
                (account_id, product_id)
                for account_id, product_id, url in self.session.execute(
                    manual_destinations(
                        tuple_(
                            PartnerManualProduct.account_id, PartnerManualProduct.product_id
                        ).in_(missing)
                    ).with_only_columns(
                        PartnerManualProduct.account_id,
                        PartnerManualProduct.product_id,
                        PartnerProduct.product_url,
                    )
                )
                if devfeed_outbound_url(url) is not None
            )
        self._unavailable.update(pairs - available)
        return available
