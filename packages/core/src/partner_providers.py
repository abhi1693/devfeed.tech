"""Creation presets for configurable partner API connections."""

from typing import Literal

from devfeed_core.schemas import ORMModel


class PartnerProviderOut(ORMModel):
    provider: str
    name: str
    partnership_type: Literal["launch_platform"]
    api_url: str
    description: str


PARTNER_PRESETS = {
    "nick-launches": PartnerProviderOut(
        provider="nick-launches",
        name="Nick Launches",
        partnership_type="launch_platform",
        api_url="https://nicklaunches.com/api/v1/products/",
        description="Sync developer products from Nick Launches.",
    )
}
