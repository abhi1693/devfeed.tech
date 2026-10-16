"""Account-owned advertising link relationships and live tier enforcement."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field
from sqlalchemy import Uuid, and_, case, cast, func, literal, select, union
from sqlalchemy.dialects.postgresql import JSONB

from devfeed_core.partner_tiers import TIER_POLICIES, tier_policy

LinkRel = Literal["nofollow", "sponsored", "ugc", "external"]
LINK_REL_OPTIONS: tuple[LinkRel, ...] = ("nofollow", "sponsored", "ugc", "external")


def unique_link_rel(values: list[LinkRel]) -> list[LinkRel]:
    if len(values) != len(set(values)):
        raise ValueError("Link relationships must be unique")
    return values


LinkRelationships = Annotated[
    list[LinkRel], Field(max_length=len(LINK_REL_OPTIONS)), AfterValidator(unique_link_rel)
]


def default_link_rel() -> list[LinkRel]:
    return ["nofollow"]


def validate_link_rel(
    tier: str, values: list[LinkRel] | None, *, previous: list[LinkRel] | None = None
) -> None:
    """Lower tiers may restore the default or product inheritance, never customize."""
    if (
        values is not None
        and values != previous
        and values != ["nofollow"]
        and not tier_policy(tier)["configurable_link_rel"]
    ):
        raise ValueError("Custom ad link relationships require Platinum or Diamond")


def configured_link_rel(account, policy=None) -> list[LinkRel]:
    """Read saved choices without persisting a temporary tier restriction."""
    override = policy.link_rel if policy is not None else None
    configured = override if override is not None else account.link_rel
    return default_link_rel() if configured is None else list(configured)


def effective_link_rel(account, policy=None) -> list[LinkRel]:
    """Resolve overrides without SQL and reapply entitlements after tier changes."""
    if not tier_policy(account.tier)["configurable_link_rel"]:
        return default_link_rel()
    return configured_link_rel(account, policy)


def sponsored_article_link_rel(session, article_ids) -> dict:
    """Aggregate current sponsor policies for a result page in one bounded read.

    Sponsorship disclosures survive campaign expiry. Their link relationships
    follow the sponsor's current tier and account/product configuration instead
    of storing policy snapshots on articles.

    Partial article indexes restrict both association reads to sponsored-content
    inventory; ordinary product catalogs add no rows to this aggregation.
    """
    from devfeed_core.models import (
        PartnerAccount,
        PartnerAsset,
        PartnerCampaign,
        PartnerProductPromotion,
    )

    if not article_ids:
        return {}
    issued = select(
        PartnerAsset.article_id.label("article_id"),
        PartnerAsset.account_id.label("account_id"),
        PartnerAsset.product_id.label("product_id"),
    ).where(
        PartnerAsset.article_id.in_(article_ids),
        PartnerAsset.ad_format == "sponsored_content",
    )
    waiting = select(
        PartnerCampaign.article_id,
        PartnerCampaign.account_id,
        cast(literal(None), Uuid).label("product_id"),
    ).where(
        PartnerCampaign.article_id.in_(article_ids),
        PartnerCampaign.ad_format == "sponsored_content",
        PartnerCampaign.status == "active",
        ~select(PartnerAsset.id)
        .where(
            PartnerAsset.campaign_id == PartnerCampaign.id,
            PartnerAsset.article_id == PartnerCampaign.article_id,
            PartnerAsset.ad_format == "sponsored_content",
        )
        .exists(),
    )
    associations = union(issued, waiting).subquery()
    configurable_tiers = [
        tier for tier, policy in TIER_POLICIES.items() if policy["configurable_link_rel"]
    ]
    configured = case(
        (
            PartnerAccount.tier.in_(configurable_tiers),
            func.coalesce(PartnerProductPromotion.link_rel, PartnerAccount.link_rel),
        ),
        else_=literal(["nofollow"], type_=JSONB),
    )
    # A downgrade must override saved custom values, including an explicit [].
    flags = [func.bool_or(configured.bool_op("?")(token)) for token in LINK_REL_OPTIONS]
    rows = session.execute(
        select(associations.c.article_id, *flags)
        .join(PartnerAccount, PartnerAccount.id == associations.c.account_id)
        .outerjoin(
            PartnerProductPromotion,
            and_(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == associations.c.product_id,
            ),
        )
        .group_by(associations.c.article_id)
    )
    return {
        article_id: [
            token for token, included in zip(LINK_REL_OPTIONS, values, strict=True) if included
        ]
        for article_id, *values in rows
    }
