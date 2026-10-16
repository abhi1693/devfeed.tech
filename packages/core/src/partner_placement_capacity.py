"""Shared delivery accounting for advisory and locked campaign capacity checks."""

from datetime import timedelta

from sqlalchemy import func, or_, select

from devfeed_core.models import PartnerAsset, PartnerDeliveryEvent, PartnerPlacementDelivery
from devfeed_core.partner_tracking import RECEIPT_TTL


def campaign_delivery_usage(campaign_ids, now):
    """Count impressions and valid reservations once per delivery for these campaigns."""
    delivered = (
        select(PartnerDeliveryEvent.delivery_id)
        .where(
            PartnerDeliveryEvent.asset_id == PartnerPlacementDelivery.asset_id,
            PartnerDeliveryEvent.delivery_id == PartnerPlacementDelivery.delivery_id,
            PartnerDeliveryEvent.kind == "impression",
            PartnerDeliveryEvent.day == now.date(),
        )
        .exists()
    )
    return (
        select(PartnerAsset.campaign_id, func.count().label("used"))
        .select_from(PartnerPlacementDelivery)
        .join(PartnerAsset)
        .where(
            PartnerAsset.campaign_id.in_(campaign_ids),
            PartnerPlacementDelivery.issued_at
            >= now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(seconds=RECEIPT_TTL),
            or_(
                delivered,
                PartnerPlacementDelivery.issued_at > now - timedelta(seconds=RECEIPT_TTL),
            ),
        )
        .group_by(PartnerAsset.campaign_id)
    )


def campaign_capacity(session, campaign, now):
    if campaign.daily_impression_limit is None:
        return True
    # The caller holds the campaign lock, serializing issuance across its products.
    # Recount here even when an earlier advisory batch found available capacity.
    usage = campaign_delivery_usage([campaign.id], now).subquery()
    used = session.scalar(select(usage.c.used)) or 0
    return used < campaign.daily_impression_limit
