"""Account inheritance, campaign targeting and immutable automatic attribution."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerProduct,
    PartnerProductPromotion,
)

PROMOTION_SURFACES = ("article", "topic", "feed")


def associated_products_query(account_id: uuid.UUID | None = None):
    """Live resolved listings establish ownership, independently of catalog identity."""
    query = (
        select(PartnerAccount, PartnerProduct)
        .join(PartnerConnection, PartnerConnection.account_id == PartnerAccount.id)
        .join(PartnerListing, PartnerListing.provider == PartnerConnection.provider)
        .join(PartnerProduct, PartnerProduct.id == PartnerListing.product_id)
        .where(
            PartnerConnection.enabled.is_(True),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
            PartnerProduct.merged_into_id.is_(None),
        )
        .distinct()
    )
    if account_id is not None:
        query = query.where(PartnerAccount.id == account_id)
    return query


def campaign_is_live(campaign: PartnerCampaign, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return campaign.status == "active" and campaign.starts_at <= now < campaign.ends_at


def campaign_matches_product(
    campaign: PartnerCampaign,
    product: PartnerProduct,
    topic_ids: Iterable[uuid.UUID | str] | None = None,
) -> bool:
    """OR within each filter, AND between technologies and prepared product topics.

    Attribution preparation omits topic_ids; serving must provide the prepared
    product topics to enforce topic targeting alongside current contextual relevance.
    """
    if campaign.targeting == "all":
        return True
    if campaign.targeting == "selected":
        return str(product.id) in {str(value) for value in campaign.product_ids}
    if campaign.targeting != "filters":
        return False
    if campaign.technologies:
        wanted = {value.casefold().strip() for value in campaign.technologies}
        available = {value.casefold().strip() for value in product.technologies}
        if not wanted.intersection(available):
            return False
    if (
        campaign.topic_ids
        and topic_ids is not None
        and not {str(value) for value in campaign.topic_ids}.intersection(
            str(value) for value in topic_ids
        )
    ):
        return False
    return bool(campaign.technologies or campaign.topic_ids)


def ensure_automatic_assets(
    session: Session,
    account_id: uuid.UUID | None = None,
    now: datetime | None = None,
    product_ids: Iterable[uuid.UUID] | None = None,
) -> int:
    """Create inherited attribution idempotently; never rewrite historical assets.

    Current eligibility is always rechecked at delivery. An account/catalog pause,
    withdrawal or expired agreement leaves the old asset available for reporting.
    Explicit selected campaigns can include products when automatic inclusion is off.
    """
    now = now or datetime.now(UTC)
    query = associated_products_query(account_id).where(
        PartnerAccount.status == "active",
        PartnerProduct.status == "approved",
        PartnerProduct.excluded.is_(False),
    )
    if product_ids is not None:
        query = query.where(PartnerProduct.id.in_(list(product_ids)))
    rows = session.execute(query).all()
    if not rows:
        return 0
    account_ids = {account.id for account, _ in rows}
    campaigns: dict[uuid.UUID, list[PartnerCampaign]] = {}
    for campaign in session.scalars(
        select(PartnerCampaign).where(
            PartnerCampaign.account_id.in_(account_ids),
            PartnerCampaign.status == "active",
            PartnerCampaign.starts_at <= now,
            PartnerCampaign.ends_at > now,
        )
    ):
        campaigns.setdefault(campaign.account_id, []).append(campaign)
    excluded = set(
        session.execute(
            select(PartnerProductPromotion.account_id, PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id.in_(account_ids),
                PartnerProductPromotion.excluded.is_(True),
            )
        ).all()
    )
    values = []
    for account, product in rows:
        if (account.id, product.id) in excluded:
            continue
        if account.organic_enabled and account.auto_include_products:
            values.append(_asset_values(account.id, product, None))
        for campaign in campaigns.get(account.id, []):
            if not account.auto_include_products and campaign.targeting != "selected":
                continue
            if campaign_matches_product(campaign, product):
                values.append(_asset_values(account.id, product, campaign.id))
    if not values:
        return 0
    # Unique partial indexes serialize retries and overlapping workers safely.
    result = session.execute(
        insert(PartnerAsset).values(values).on_conflict_do_nothing().returning(PartnerAsset.id)
    )
    return len(result.all())


def _asset_values(account_id: uuid.UUID, product: PartnerProduct, campaign_id: uuid.UUID | None):
    return {
        "id": uuid.uuid4(),
        "account_id": account_id,
        "product_id": product.id,
        "campaign_id": campaign_id,
        "automatic": True,
        "name": product.name,
        "kind": "ad" if campaign_id else "product",
        "status": "active",
        "created_at": datetime.now(UTC),
    }
