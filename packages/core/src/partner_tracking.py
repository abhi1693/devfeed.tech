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
from sqlalchemy.exc import SQLAlchemyError

from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDeliveryEvent,
    PartnerPlacementDelivery,
    PartnerProduct,
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


class BusyPartnerAsset(SQLAlchemyError):
    """A valid receipt can be retried after a concurrent attribution edit commits."""


def record_delivery_event(session, receipt: DeliveryReceipt, kind: EventKind) -> bool:
    from devfeed_core.partner_placements import automatic_asset_available, lock_attribution_row

    # Resolve the immutable owner first, then acquire the same nonblocking lock
    # order as issuance. A busy row produces retriable HTTP 503, never a false 403.
    account_id = session.scalar(
        select(PartnerAsset.account_id).where(PartnerAsset.id == receipt.asset_id)
    )
    if account_id is None:
        raise InactivePartnerAsset
    account = lock_attribution_row(session, PartnerAccount, account_id, busy_error=True)
    asset_row = lock_attribution_row(session, PartnerAsset, receipt.asset_id, busy_error=True)
    if account.status != "active" or asset_row.status != "active":
        raise InactivePartnerAsset
    # Automatic deliveries must exist in the trusted issuer's durable outbox and
    # still satisfy account/product/campaign policy when the browser reports them.
    if asset_row.automatic:
        reservation = session.get(PartnerPlacementDelivery, receipt.delivery_id)
        product = lock_attribution_row(
            session, PartnerProduct, asset_row.product_id, busy_error=True
        )
        if (
            reservation is None
            or reservation.asset_id != asset_row.id
            or int(reservation.issued_at.timestamp()) != int(receipt.issued_at.timestamp())
            or not automatic_asset_available(
                session,
                asset_row,
                account,
                product,
                datetime.now(UTC),
                busy_error=True,
                surface=getattr(reservation, "surface", None),
            )
        ):
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
