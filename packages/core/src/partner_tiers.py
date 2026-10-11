"""Code-defined promotion entitlements, separate from catalog trust and membership."""

from typing import Literal, TypedDict

PartnerTier = Literal["bronze", "silver", "gold", "platinum", "diamond"]
PromotionKind = Literal["product", "ad"]


class TierPolicy(TypedDict):
    allowed_promotion_kinds: tuple[PromotionKind, ...]
    default_promotion_kind: PromotionKind


TIER_POLICIES: dict[str, TierPolicy] = {
    tier: {
        "allowed_promotion_kinds": ("product", "ad") if paid else ("product",),
        "default_promotion_kind": "ad" if paid else "product",
    }
    for tier, paid in (
        ("bronze", False),
        ("silver", False),
        ("gold", True),
        ("platinum", True),
        ("diamond", True),
    )
}
_BRONZE = (
    "Partner portal access",
    "Product and ad performance reporting",
    "Relevant product recommendations",
)
_SILVER = (*_BRONZE, "Product promotion planning")
_GOLD = (
    *_SILVER,
    "Inherited sponsored placements",
    "Sponsored feed fallback when no relevant sponsored product matches",
)
_PLATINUM = (*_GOLD, "Priority campaign support")
_DIAMOND = (*_PLATINUM, "Custom partnership and campaign planning")
TIER_BENEFITS: dict[str, tuple[str, ...]] = dict(
    zip(TIER_POLICIES, (_BRONZE, _SILVER, _GOLD, _PLATINUM, _DIAMOND), strict=True)
)


def tier_policy(tier: str) -> TierPolicy:
    return TIER_POLICIES[tier]


def promotion_kind_allowed(account, kind: str) -> bool:
    return kind in tier_policy(account.tier)["allowed_promotion_kinds"]


def effective_promotion_kind(account) -> PromotionKind:
    policy = tier_policy(account.tier)
    override = account.promotion_kind
    if override in policy["allowed_promotion_kinds"]:
        return override
    return policy["default_promotion_kind"]
