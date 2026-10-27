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
