"""Registered reader contexts for partner delivery and account policy."""

from typing import Literal, get_args

PromotionSurface = Literal["article", "topic", "feed", "search", "header"]
PROMOTION_SURFACES = get_args(PromotionSurface)


def default_promotion_surfaces() -> list[PromotionSurface]:
    return list(PROMOTION_SURFACES)


def default_campaign_surfaces(ad_format: str) -> list[PromotionSurface]:
    if ad_format == "header_sponsorship":
        return ["header"]
    if ad_format == "sponsored_content":
        return ["feed"]
    if ad_format == "engagement":
        return [surface for surface in PROMOTION_SURFACES if surface != "header"]
    return [surface for surface in PROMOTION_SURFACES if surface not in {"search", "header"}]
