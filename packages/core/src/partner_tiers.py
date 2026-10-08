"""Ordered partnership tiers and their code-defined commercial benefits.

Benefits describe the partnership offering; authentication and membership access
remain independent of tier. Changing this catalog requires a code deployment.
"""

from typing import Literal

PartnerTier = Literal["bronze", "silver", "gold", "platinum", "diamond"]

_BRONZE = ("Partner portal access", "Product and ad performance reporting")
_SILVER = (*_BRONZE, "Product placement opportunities")
_GOLD = (*_SILVER, "Sponsored ad campaign opportunities")
_PLATINUM = (*_GOLD, "Priority campaign support")
_DIAMOND = (*_PLATINUM, "Custom partnership and campaign planning")

TIER_BENEFITS: dict[str, tuple[str, ...]] = {
    "bronze": _BRONZE,
    "silver": _SILVER,
    "gold": _GOLD,
    "platinum": _PLATINUM,
    "diamond": _DIAMOND,
}
