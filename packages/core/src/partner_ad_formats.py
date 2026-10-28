"""Creative formats are separate from commercial attribution and contextual surfaces."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, get_args

from devfeed_core.models import PartnerAccount

from .partner_tiers import tier_policy

AdFormat = Literal["feed", "header_sponsorship", "sponsored_content", "engagement"]


def default_ad_formats() -> list[AdFormat]:
    return ["feed"]


AD_FORMATS = get_args(AdFormat)
AUTOMATIC_AD_FORMATS = ("feed", "header_sponsorship", "engagement")


def ad_format_allowed(account: PartnerAccount, ad_format: str) -> bool:
    return ad_format in tier_policy(account.tier)["allowed_ad_formats"]


def validate_account_formats(
    tier: str,
    formats: Sequence[str],
    *,
    previous: Sequence[str] | None = None,
) -> None:
    if len(formats) != len(set(formats)) or not set(formats).issubset(AUTOMATIC_AD_FORMATS):
        raise ValueError(
            "Choose unique automatic ad formats; sponsored content requires a campaign"
        )
    allowed = set(tier_policy(tier)["allowed_ad_formats"])
    if not set(formats).issubset(allowed) and formats != previous:
        raise ValueError("This ad format requires Silver or a higher partnership tier")


def validate_product_formats(
    tier: str,
    formats: Sequence[str] | None,
    *,
    previous: Sequence[str] | None = None,
) -> None:
    if formats is None:
        return
    if len(formats) != len(set(formats)) or not set(formats).issubset(AD_FORMATS):
        raise ValueError("Choose unique product ad formats")
    if not set(formats).issubset(tier_policy(tier)["allowed_ad_formats"]) and formats != previous:
        raise ValueError("This ad format requires Silver or a higher partnership tier")


class PlacementFormatQuery(Protocol):
    @property
    def ad_formats(self) -> Sequence[str]: ...

    @property
    def surface(self) -> str: ...


def placement_format_allowed(ad_format: str, query: PlacementFormatQuery) -> bool:
    if ad_format not in query.ad_formats:
        return False
    if query.surface == "header":
        return ad_format == "header_sponsorship"
    if query.surface == "search":
        return ad_format == "engagement"
    if ad_format == "header_sponsorship":
        return False
    if ad_format == "sponsored_content":
        return query.surface == "feed"
    return ad_format in {"feed", "engagement"}
