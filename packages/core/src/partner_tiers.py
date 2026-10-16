"""Code-defined promotion entitlements, separate from catalog trust and membership."""

from typing import Literal, TypedDict

PartnerTier = Literal["bronze", "silver", "gold", "platinum", "diamond"]
PromotionKind = Literal["product", "ad"]


class TierPolicy(TypedDict):
    allowed_promotion_kinds: tuple[PromotionKind, ...]
    default_promotion_kind: PromotionKind
    advanced_targeting: bool
    allowed_ad_formats: tuple[str, ...]
    configurable_link_rel: bool


TIER_POLICIES: dict[str, TierPolicy] = {
    tier: {
        "allowed_promotion_kinds": ("product", "ad") if paid else ("product",),
        "default_promotion_kind": "ad" if paid else "product",
        "advanced_targeting": tier in {"platinum", "diamond"},
        "configurable_link_rel": tier in {"platinum", "diamond"},
        "allowed_ad_formats": ("feed", "header_sponsorship", "sponsored_content", "engagement")
        if paid
        else ("feed",),
    }
    for tier, paid in (
        ("bronze", False),
        ("silver", True),
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
_SILVER = (
    *_BRONZE,
    "Inherited sponsored placements",
    "Native feed ads, homepage sponsorship, sponsored content and engagement ads",
    "Sponsored feed fallback when no relevant sponsored product matches",
)
_GOLD = (
    *_SILVER,
    "Product promotion planning",
)
_PLATINUM = (
    *_GOLD,
    "Audience targeting by experience, programming languages and developer tools",
    "Configurable ad link relationships for accounts and products",
    "Priority campaign support",
)
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
