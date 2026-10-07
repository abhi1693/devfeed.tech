"""Anonymous, delivery-bound partner events and atomic daily counters."""

import hashlib
import hmac
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDeliveryEvent,
    PartnerTrackedDailyMetric,
)

EventKind = Literal["impression", "click"]
RECEIPT_TTL = 3600
_RECEIPT = re.compile(r"v1\.([0-9a-f]{32})\.([0-9a-f]{32})\.([0-9]{1,12})\.([0-9a-f]{64})")


@dataclass(frozen=True)
class DeliveryReceipt:
    asset_id: uuid.UUID
    delivery_id: uuid.UUID
    issued_at: datetime


class PartnerTrackingTokens:
    def __init__(self, key: str, *, clock: Callable[[], float] = time.time):
        if len(key.encode()) < 32:
            raise ValueError("Partner tracking key must contain at least 32 bytes")
        self.key = hmac.digest(key.encode(), b"devfeed:partner-delivery:v1", "sha256")
        self.clock = clock

    def _signature(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self, asset_id: uuid.UUID, *, delivery_id: uuid.UUID | None = None) -> str:
        """Called by trusted delivery code once per placement render, never by a browser."""
        payload = f"v1.{asset_id.hex}.{(delivery_id or uuid.uuid4()).hex}.{int(self.clock())}"
        return f"{payload}.{self._signature(payload)}"

    def verify(self, token: str) -> DeliveryReceipt | None:
        match = _RECEIPT.fullmatch(token)
        if not match:
            return None
        issued = int(match[3])
        now = int(self.clock())
        if not 0 <= now - issued < RECEIPT_TTL:
            return None
        payload, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._signature(payload)):
            return None
        return DeliveryReceipt(
            uuid.UUID(hex=match[1]), uuid.UUID(hex=match[2]), datetime.fromtimestamp(issued, UTC)
        )


class InactivePartnerAsset(Exception):
    pass


def record_delivery_event(session, receipt: DeliveryReceipt, kind: EventKind) -> bool:
    # Fence event writes against account/asset pauses, including concurrent edits.
    asset = session.scalar(
        select(PartnerAsset.id)
        .join(PartnerAccount)
        .where(
            PartnerAsset.id == receipt.asset_id,
            PartnerAsset.status == "active",
            PartnerAccount.status == "active",
        )
        .with_for_update(of=[PartnerAsset, PartnerAccount])
    )
    if asset is None:
        raise InactivePartnerAsset
    day = receipt.issued_at.date()
    created = session.scalar(
        insert(PartnerDeliveryEvent)
        .values(asset_id=receipt.asset_id, delivery_id=receipt.delivery_id, kind=kind, day=day)
        .on_conflict_do_nothing()
        .returning(PartnerDeliveryEvent.delivery_id)
    )
    if created is None:
        return False
    impressions, clicks = int(kind == "impression"), int(kind == "click")
    session.execute(
        insert(PartnerTrackedDailyMetric)
        .values(
            asset_id=receipt.asset_id,
            day=day,
            impressions=impressions,
            clicks=clicks,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=[PartnerTrackedDailyMetric.asset_id, PartnerTrackedDailyMetric.day],
            set_={
                "impressions": PartnerTrackedDailyMetric.impressions + impressions,
                "clicks": PartnerTrackedDailyMetric.clicks + clicks,
                "updated_at": datetime.now(UTC),
            },
        )
    )
    return True
