"""Anonymous, delivery-bound partner receipt claims."""

import hashlib
import hmac
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerPlacementDelivery,
    PartnerProduct,
    utcnow,
)

EventKind = Literal["impression", "click"]
RECEIPT_TTL = 3600
DELIVERY_HISTORY_RETENTION = timedelta(days=2)
DELIVERY_HISTORY_BATCH_SIZE = 4000
_RECEIPT = re.compile(r"v1\.([0-9a-f]{32})\.([0-9a-f]{32})\.([0-9]{1,12})\.([0-9a-f]{64})")


@dataclass(frozen=True)
class DeliveryReceipt:
    asset_id: uuid.UUID
    delivery_id: uuid.UUID
    issued_at: datetime


class PartnerTrackingTokens:
    def __init__(self, key: str, *, clock: Callable[[], float] = time.time) -> None:
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


@dataclass(frozen=True)
class DeliveryClaim:
    """An operational deduplication claim and its stable observation time."""

    recorded_at: datetime
    created: bool

    def __bool__(self) -> bool:
        return self.created


class InactivePartnerAsset(Exception):
    pass


class BusyPartnerAsset(SQLAlchemyError):
    """A valid receipt can be retried after a concurrent attribution edit commits."""


def prune_partner_delivery_history(
    factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
    batch_size: int = DELIVERY_HISTORY_BATCH_SIZE,
) -> int:
    """Prune expired operational receipt reservations in bounded batches."""
    if not 1 <= batch_size <= DELIVERY_HISTORY_BATCH_SIZE:
        raise ValueError(f"Partner delivery cleanup batch must be 1..{DELIVERY_HISTORY_BATCH_SIZE}")
    now = (now or utcnow()).astimezone(UTC)
    cutoff = now - DELIVERY_HISTORY_RETENTION
    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(832604284))):
            return 0
        expired_reservations = (
            select(PartnerPlacementDelivery.delivery_id)
            .where(PartnerPlacementDelivery.issued_at < cutoff)
            .order_by(PartnerPlacementDelivery.issued_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("expired_partner_reservations")
            .prefix_with("MATERIALIZED")
        )
        reservations = session.execute(
            delete(PartnerPlacementDelivery)
            .where(PartnerPlacementDelivery.delivery_id.in_(select(expired_reservations)))
            .execution_options(synchronize_session=False)
        )
        return cast(CursorResult[*tuple[object, ...]], reservations).rowcount


def record_delivery_event(
    session: Session, receipt: DeliveryReceipt, kind: EventKind
) -> DeliveryClaim:
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
    assert account is not None and asset_row is not None
    if account.status != "active" or asset_row.status != "active":
        raise InactivePartnerAsset
    # Automatic deliveries must exist in the trusted issuer's durable outbox and
    # still satisfy account/product/campaign policy when the browser reports them.
    if asset_row.automatic:
        reservation = session.get(PartnerPlacementDelivery, receipt.delivery_id)
        assert asset_row.product_id is not None
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
    if not asset_row.automatic:
        # Manual placements have no issuance-time reservation; create the same
        # short-lived operational identity used to prevent receipt replays.
        session.execute(
            insert(PartnerPlacementDelivery)
            .values(
                delivery_id=receipt.delivery_id,
                asset_id=receipt.asset_id,
                issued_at=receipt.issued_at,
            )
            .on_conflict_do_nothing()
        )
    column = (
        PartnerPlacementDelivery.impression_recorded_at
        if kind == "impression"
        else PartnerPlacementDelivery.click_recorded_at
    )
    recorded_at = session.scalar(
        update(PartnerPlacementDelivery)
        .where(
            PartnerPlacementDelivery.delivery_id == receipt.delivery_id,
            PartnerPlacementDelivery.asset_id == receipt.asset_id,
            column.is_(None),
        )
        .values({column.key: datetime.now(UTC)})
        .returning(column)
    )
    if recorded_at is not None:
        return DeliveryClaim(recorded_at, True)
    reservation = session.get(PartnerPlacementDelivery, receipt.delivery_id)
    if reservation is None or reservation.asset_id != receipt.asset_id:
        raise InactivePartnerAsset
    recorded_at = getattr(reservation, column.key)
    if recorded_at is None:
        raise InactivePartnerAsset
    return DeliveryClaim(recorded_at, False)
