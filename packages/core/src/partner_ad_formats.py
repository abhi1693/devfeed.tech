"""Creative formats are separate from commercial attribution and contextual surfaces."""

from collections.abc import Sequence
from typing import Literal, get_args

from .partner_tiers import tier_policy

AdFormat = Literal["feed", "homepage_sponsorship", "sponsored_content", "engagement"]


def default_ad_formats() -> list[AdFormat]:
    return ["feed"]


AD_FORMATS = get_args(AdFormat)
AUTOMATIC_AD_FORMATS = ("feed", "homepage_sponsorship", "engagement")


def ad_format_allowed(account, ad_format: str) -> bool:
    return ad_format in tier_policy(account.tier)["allowed_ad_formats"]


def validate_account_formats(tier: str, formats: Sequence[str], *, previous=None):
    if len(formats) != len(set(formats)) or not set(formats).issubset(AUTOMATIC_AD_FORMATS):
        raise ValueError(
            "Choose unique automatic ad formats; sponsored content requires a campaign"
        )
    allowed = set(tier_policy(tier)["allowed_ad_formats"])
    if not set(formats).issubset(allowed) and formats != previous:
        raise ValueError("This ad format requires Silver or a higher partnership tier")


def validate_product_formats(tier: str, formats: Sequence[str] | None, *, previous=None):
    if formats is None:
        return
    if len(formats) != len(set(formats)) or not set(formats).issubset(AD_FORMATS):
        raise ValueError("Choose unique product ad formats")
    if not set(formats).issubset(tier_policy(tier)["allowed_ad_formats"]) and formats != previous:
        raise ValueError("This ad format requires Silver or a higher partnership tier")


def placement_format_allowed(ad_format: str, query) -> bool:
    if ad_format not in query.ad_formats:
        return False
    if ad_format == "homepage_sponsorship":
        return query.surface == "feed" and query.allow_sponsored_fallback
    if ad_format == "sponsored_content":
        return query.surface == "feed"
    return ad_format in {"feed", "engagement"}
