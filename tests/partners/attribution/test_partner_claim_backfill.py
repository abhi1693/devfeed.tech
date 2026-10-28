"""Legacy receipts become short-lived operational claims without new analytics rows."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from devfeed_core.models import PartnerAccount, PartnerAsset, PartnerPlacementDelivery
from devfeed_core.partner_claim_backfill import LEGACY_CLAIM_AT, backfill_partner_claims
from devfeed_core.partner_tracking import DeliveryReceipt, record_delivery_event


@pytest.mark.integration
def test_claim_backfill_is_idempotent_and_preserves_newer_claims(database):
    now = datetime.now(UTC)
    with database.begin() as session:
        account = PartnerAccount(name="Partner", tier="bronze")
        session.add(account)
        session.flush()
        asset = PartnerAsset(account_id=account.id, name="Ad", kind="ad", status="active")
        session.add(asset)
        session.flush()
        asset_id, existing, manual = asset.id, uuid.uuid4(), uuid.uuid4()
        session.add(
            PartnerPlacementDelivery(
                delivery_id=existing,
                asset_id=asset_id,
                issued_at=now,
                impression_recorded_at=now,
            )
        )
    records = [
        {
            "asset_id": str(asset_id),
            "delivery_id": str(existing),
            "kind": "impression",
            "day": now.date().isoformat(),
        },
        {
            "asset_id": str(asset_id),
            "delivery_id": str(existing),
            "kind": "click",
            "day": now.date().isoformat(),
        },
        {
            "asset_id": str(asset_id),
            "delivery_id": str(manual),
            "kind": "impression",
            "day": now.date().isoformat(),
        },
    ]

    async def run():
        url = os.environ["DEVFEED_TEST_DATABASE_URL"].replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
            await backfill_partner_claims(connection, records)
            await backfill_partner_claims(connection, records)

    asyncio.run(run())
    with database.begin() as session:
        first = session.get(PartnerPlacementDelivery, existing)
        second = session.get(PartnerPlacementDelivery, manual)
        assert first.impression_recorded_at == now
        assert first.click_recorded_at == LEGACY_CLAIM_AT
        assert second.impression_recorded_at == LEGACY_CLAIM_AT
        assert second.click_recorded_at is None
        claim = record_delivery_event(session, DeliveryReceipt(asset_id, manual, now), "impression")
        assert not claim and claim.recorded_at == LEGACY_CLAIM_AT
        fresh = record_delivery_event(session, DeliveryReceipt(asset_id, manual, now), "click")
        assert fresh and fresh.recorded_at > LEGACY_CLAIM_AT
        retry = record_delivery_event(session, DeliveryReceipt(asset_id, manual, now), "click")
        assert not retry and retry.recorded_at == fresh.recorded_at


@pytest.mark.integration
def test_conflicting_legacy_owner_rejects_whole_page(database):
    now = datetime.now(UTC)
    with database.begin() as session:
        account = PartnerAccount(name="Partner", tier="bronze")
        session.add(account)
        session.flush()
        first = PartnerAsset(account_id=account.id, name="One", kind="ad", status="active")
        second = PartnerAsset(account_id=account.id, name="Two", kind="ad", status="active")
        session.add_all([first, second])
        session.flush()
        first_id, second_id, conflict, fresh = first.id, second.id, uuid.uuid4(), uuid.uuid4()
        session.add(
            PartnerPlacementDelivery(
                delivery_id=conflict, asset_id=first_id, issued_at=now - timedelta(minutes=1)
            )
        )
    records = [
        {
            "asset_id": str(first_id),
            "delivery_id": str(fresh),
            "kind": "click",
            "day": now.date().isoformat(),
        },
        {
            "asset_id": str(second_id),
            "delivery_id": str(conflict),
            "kind": "click",
            "day": now.date().isoformat(),
        },
    ]

    async def run():
        url = os.environ["DEVFEED_TEST_DATABASE_URL"].replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
            with pytest.raises(RuntimeError, match="Conflicting"):
                await backfill_partner_claims(connection, records)

    asyncio.run(run())
    with database() as session:
        assert session.get(PartnerPlacementDelivery, fresh) is None
        assert session.get(PartnerPlacementDelivery, conflict).click_recorded_at is None


class ClaimConnection:
    """Track a complete page's atomic transaction boundary without a database."""

    def __init__(self, rows=(), error=None):
        self.rows = rows
        self.error = error
        self.events = []
        self.parameters = None
        self.active = False

    def transaction(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def scope():
            self.active = True
            self.events.append("begin")
            try:
                yield
            except Exception:
                self.events.append("rollback")
                raise
            else:
                self.events.append("commit")
            finally:
                self.active = False

        return scope()

    async def execute(self, statement, parameters):
        assert self.active
        self.events.append("write")
        self.parameters = parameters
        if self.error:
            raise self.error

        async def fetchall():
            assert self.active
            return self.rows

        from types import SimpleNamespace

        return SimpleNamespace(fetchall=fetchall)


def historical_receipt(**updates):
    return (
        dict(
            asset_id=uuid.UUID(int=1),
            delivery_id=uuid.UUID(int=2),
            day="2026-01-01",
            kind="impression",
        )
        | updates
    )


def test_claim_page_groups_both_event_kinds_and_replays_a_stable_sentinel():
    async def run():
        connection = ClaimConnection(
            [{"delivery_id": uuid.UUID(int=2)}, {"delivery_id": uuid.UUID(int=3)}]
        )
        page = [
            historical_receipt(),
            historical_receipt(kind="click"),
            historical_receipt(
                delivery_id=uuid.UUID(int=3), day=datetime(2026, 1, 1, tzinfo=UTC).date()
            ),
        ]
        await backfill_partner_claims(connection, page)
        parameters = list(connection.parameters)
        assert parameters[:5] == [
            uuid.UUID(int=1),
            uuid.UUID(int=2),
            datetime(2026, 1, 1, tzinfo=UTC),
            LEGACY_CLAIM_AT,
            LEGACY_CLAIM_AT,
        ]
        assert parameters[8:] == [LEGACY_CLAIM_AT, None]
        assert connection.events == ["begin", "write", "commit"]
        await backfill_partner_claims(connection, page)
        assert parameters == connection.parameters
        assert connection.events == ["begin", "write", "commit"] * 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "records", [None, {}, "invalid", [{}] * 1001, [None], [{}], [{"private": "not allowed"}]]
)
def test_invalid_claim_pages_are_rejected_without_starting_a_transaction(records):
    async def run():
        connection = ClaimConnection()
        with pytest.raises(ValueError):
            await backfill_partner_claims(connection, records)
        assert connection.events == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("asset_id", "invalid"),
        ("delivery_id", "invalid"),
        ("day", "2026-02-31"),
        ("day", None),
        ("day", datetime(2026, 1, 1, tzinfo=UTC)),
        ("kind", "view"),
        ("kind", []),
    ],
)
def test_invalid_receipt_identities_dates_and_kinds_reject_the_whole_page(field, value):
    async def run():
        connection = ClaimConnection()
        page = [historical_receipt(), historical_receipt(**{field: value})]
        with pytest.raises(ValueError):
            await backfill_partner_claims(connection, page)
        assert connection.events == []

    asyncio.run(run())


def test_inconsistent_receipt_days_do_not_create_partial_claims():
    async def run():
        connection = ClaimConnection()
        with pytest.raises(ValueError, match="Inconsistent"):
            await backfill_partner_claims(
                connection, [historical_receipt(), historical_receipt(day="2026-01-02")]
            )
        assert connection.events == []
        await backfill_partner_claims(connection, [])
        assert connection.events == []

    asyncio.run(run())


@pytest.mark.parametrize("error", [None, RuntimeError("write interrupted")])
def test_claim_owner_conflicts_and_write_errors_rollback_before_page_acknowledgement(error):
    async def run():
        connection = ClaimConnection([], error=error)
        with pytest.raises(RuntimeError, match="write interrupted" if error else "Conflicting"):
            await backfill_partner_claims(connection, [historical_receipt()])
        assert connection.events == ["begin", "write", "rollback"]
        assert not connection.active

    asyncio.run(run())
