"""Account inheritance, campaign targeting and immutable automatic attribution."""

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, exists, func, or_, select
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
from .partner_tiers import effective_promotion_kind, promotion_kind_allowed
from .partner_tools import eligible

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


def trusted_product_exists():
    """Correlated account-owned acceptance, usable independently of factual qualification."""
    return exists(
        select(PartnerProductPromotion.product_id)
        .join(PartnerAccount, PartnerAccount.id == PartnerProductPromotion.account_id)
        .join(PartnerConnection, PartnerConnection.account_id == PartnerAccount.id)
        .join(PartnerListing, PartnerListing.provider == PartnerConnection.provider)
        .where(
            PartnerProductPromotion.product_id == PartnerProduct.id,
            PartnerProductPromotion.commercial_approved_at.is_not(None),
            PartnerProductPromotion.approval_source == "trusted",
            PartnerProductPromotion.excluded.is_(False),
            PartnerAccount.status == "active",
            PartnerAccount.approval_mode == "trusted",
            PartnerConnection.enabled.is_(True),
            PartnerListing.product_id == PartnerProduct.id,
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
    )


def commercially_eligible(session, account, product, now, *, busy_error=False):
    if product.merged_into_id or product.excluded:
        return False
    if eligible(product, now):
        return True
    if account.approval_mode != "trusted":
        return False
    policy = select(PartnerProductPromotion).where(
        PartnerProductPromotion.account_id == account.id,
        PartnerProductPromotion.product_id == product.id,
    )
    approval = session.scalar(policy.with_for_update(read=True, skip_locked=True))
    if approval is None and busy_error and session.scalar(policy) is not None:
        from .partner_tracking import BusyPartnerAsset

        raise BusyPartnerAsset("Partner commercial acceptance is being updated")
    return bool(
        approval
        and not approval.excluded
        and approval.commercial_approved_at
        and approval.approval_source == "trusted"
    )


def ensure_managed_campaign(session, account, now):
    if effective_promotion_kind(account) != "ad" or not promotion_kind_allowed(account, "ad"):
        return None
    campaign_id = session.scalar(
        insert(PartnerCampaign)
        .values(
            id=uuid.uuid4(),
            account_id=account.id,
            name="Inherited sponsored placement",
            managed=True,
            status="active",
            targeting="all",
            product_ids=[],
            topic_ids=[],
            technologies=[],
            surfaces=list(PROMOTION_SURFACES),
            starts_at=now,
            ends_at=datetime(9999, 12, 31, tzinfo=UTC),
            priority=1,
            created_at=now,
        )
        .on_conflict_do_nothing()
        .returning(PartnerCampaign.id)
    )
    return campaign_id or session.scalar(
        select(PartnerCampaign.id).where(
            PartnerCampaign.account_id == account.id, PartnerCampaign.managed.is_(True)
        )
    )


def ensure_automatic_assets(
    session: Session,
    account_id: uuid.UUID | None = None,
    now: datetime | None = None,
    product_ids: Iterable[uuid.UUID] | None = None,
) -> int:
    """Enroll current owned inventory; preserve immutable historical attribution.

    Trusted commercial acceptance never changes global AI qualification or evidence.
    Tier-inherited sponsorship uses its own stable ledger alongside dated campaigns.
    """
    now = now or datetime.now(UTC)
    query = associated_products_query(account_id).where(
        PartnerAccount.status == "active",
        PartnerProduct.excluded.is_(False),
    )
    if product_ids is not None:
        query = query.where(PartnerProduct.id.in_(list(product_ids)))
    rows = session.execute(query).all()
    if not rows:
        return 0
    rows = sorted(rows, key=lambda row: (row[0].id, row[1].id))
    accounts = {account.id: account for account, _ in rows}
    for account in accounts.values():
        ensure_managed_campaign(session, account, now)
    trusted = [
        dict(
            account_id=account.id,
            product_id=product.id,
            commercial_approved_at=now,
            approval_source="trusted",
            excluded=False,
        )
        for account, product in rows
        if account.approval_mode == "trusted"
    ]
    accepted: Sequence[uuid.UUID] = []
    if trusted:
        accepted = (
            session.execute(
                insert(PartnerProductPromotion)
                .values(trusted)
                .on_conflict_do_update(
                    index_elements=[
                        PartnerProductPromotion.account_id,
                        PartnerProductPromotion.product_id,
                    ],
                    set_={"commercial_approved_at": now, "approval_source": "trusted"},
                    where=PartnerProductPromotion.commercial_approved_at.is_(None),
                )
                .returning(PartnerProductPromotion.product_id)
            )
            .scalars()
            .all()
        )
    # Queue only newly accepted products; failed/imported image jobs keep their own
    # retry and idempotency contracts, independently of the commercial ledger.
    if accepted:
        from .logos import request_logo
        from .partner_product_images import request_product_image

        for product_id in sorted(set(accepted)):
            request_logo(session, PartnerProduct, product_id, automatic=True)
            request_product_image(session, product_id, automatic=True)
    campaigns: dict[uuid.UUID, list[PartnerCampaign]] = {}
    for campaign in session.scalars(
        select(PartnerCampaign).where(
            PartnerCampaign.account_id.in_(accounts),
            PartnerCampaign.status == "active",
            PartnerCampaign.starts_at <= now,
            PartnerCampaign.ends_at > now,
        )
    ):
        campaigns.setdefault(campaign.account_id, []).append(campaign)
    excluded = set(
        session.execute(
            select(PartnerProductPromotion.account_id, PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id.in_(accounts),
                PartnerProductPromotion.excluded.is_(True),
            )
        ).all()
    )
    values = []
    for account, product in rows:
        if (account.id, product.id) in excluded or not (
            account.approval_mode == "trusted" or product.status == "approved"
        ):
            continue
        if (
            account.organic_enabled
            and account.auto_include_products
            and effective_promotion_kind(account) == "product"
        ):
            values.append(_asset_values(account.id, product, None))
        for campaign in campaigns.get(account.id, []):
            if not promotion_kind_allowed(account, "ad") and not campaign.grandfathered:
                continue
            if campaign.managed and effective_promotion_kind(account) != "ad":
                continue
            if not account.auto_include_products and campaign.targeting != "selected":
                continue
            if campaign_matches_product(campaign, product):
                values.append(_asset_values(account.id, product, campaign.id))
    if not values:
        return 0
    result = session.execute(
        insert(PartnerAsset).values(values).on_conflict_do_nothing().returning(PartnerAsset.id)
    )
    return len(result.all())


def sync_partner_promotions(factory, *, limit=200):
    """Bounded repair lane for imported inventory and newly inherited account policy."""
    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(832604283))):
            return 0
        paid_mode = or_(
            PartnerAccount.promotion_kind == "ad",
            and_(
                PartnerAccount.promotion_kind.is_(None),
                PartnerAccount.tier.in_(["gold", "platinum", "diamond"]),
            ),
        )
        has_organic = exists(
            select(PartnerAsset.id).where(
                PartnerAsset.account_id == PartnerAccount.id,
                PartnerAsset.product_id == PartnerProduct.id,
                PartnerAsset.automatic.is_(True),
                PartnerAsset.campaign_id.is_(None),
            )
        )
        has_sponsored = exists(
            select(PartnerAsset.id)
            .join(PartnerCampaign, PartnerCampaign.id == PartnerAsset.campaign_id)
            .where(
                PartnerAsset.account_id == PartnerAccount.id,
                PartnerAsset.product_id == PartnerProduct.id,
                PartnerAsset.automatic.is_(True),
                PartnerCampaign.managed.is_(True),
            )
        )
        has_asset = or_(and_(paid_mode, has_sponsored), and_(~paid_mode, has_organic))
        excluded = exists(
            select(PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
                PartnerProductPromotion.excluded.is_(True),
            )
        )
        accepted = exists(
            select(PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
                PartnerProductPromotion.commercial_approved_at.is_not(None),
            )
        )
        ids = session.scalars(
            associated_products_query()
            .with_only_columns(PartnerProduct.id, maintain_column_froms=True)
            .where(
                PartnerAccount.status == "active",
                PartnerProduct.excluded.is_(False),
                ~excluded,
                PartnerAccount.auto_include_products.is_(True),
                or_(paid_mode, PartnerAccount.organic_enabled.is_(True)),
                or_(PartnerProduct.status == "approved", PartnerAccount.approval_mode == "trusted"),
                or_(~has_asset, and_(PartnerAccount.approval_mode == "trusted", ~accepted)),
            )
            .order_by(PartnerProduct.id)
            .limit(limit)
        ).all()
        return ensure_automatic_assets(session, product_ids=ids) if ids else 0


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
