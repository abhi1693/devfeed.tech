"""Partner accounts, campaigns, and placement assets."""

import uuid
from datetime import timedelta

from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
)
from devfeed_core.partner_surfaces import default_campaign_surfaces

from tests.support.partner_matching import NOW


def account(**changes):
    return PartnerAccount(
        **{
            "id": uuid.uuid4(),
            "name": "Launch platform",
            "tier": "platinum",
            "status": "active",
            "organic_enabled": True,
            "auto_include_products": True,
            "promotion_kind": "product",
            "approval_mode": "qualified",
            "outbound_destination": "product_url",
            "link_rel": ["nofollow"],
            "promotion_surfaces": ["article", "topic", "feed", "search", "header"],
            "ad_formats": ["feed", "header_sponsorship", "engagement"],
            "audience": {},
            **changes,
        }
    )


def campaign(owner, **changes):
    return PartnerCampaign(
        **{
            "id": uuid.uuid4(),
            "account_id": owner.id,
            "name": "Autumn campaign",
            "status": "active",
            "ad_format": "feed",
            "managed": False,
            "targeting": "all",
            "product_ids": [],
            "technologies": [],
            "topic_ids": [],
            "audience": {},
            "priority": 10,
            "surfaces": default_campaign_surfaces(changes.get("ad_format", "feed")),
            "starts_at": NOW - timedelta(days=1),
            "ends_at": NOW + timedelta(days=10),
            **changes,
        }
    )


def asset(owner, value, agreement=None, **changes):
    return PartnerAsset(
        **{
            "id": uuid.uuid4(),
            "account_id": owner.id,
            "product_id": value.id,
            "campaign_id": agreement.id if agreement else None,
            "kind": "ad" if agreement else "product",
            "automatic": True,
            "status": "active",
            "ad_format": "feed",
            "article_id": None,
            **changes,
        }
    )
