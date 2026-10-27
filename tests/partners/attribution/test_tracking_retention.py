"""Bounded receipt pruning preserves ClickHouse totals and active replay claims."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import PartnerAccount, PartnerAsset, PartnerPlacementDelivery
from devfeed_core.partner_tracking import (
    PartnerTrackingTokens,
    prune_partner_delivery_history,
    record_delivery_event,
)
from pydantic import SecretStr
from sqlalchemy import func, select, text

pytestmark = pytest.mark.integration
KEY = "disposable-partner-retention-tracking-key-32-bytes"
NOW = datetime(2026, 10, 13, 0, 30, tzinfo=UTC)


def seed_asset(database):
    with database.begin() as session:
        account = PartnerAccount(name="Retention partner", tier="gold", status="active")
        session.add(account)
        session.flush()
        asset = PartnerAsset(
            account_id=account.id, name="Retained asset", kind="ad", status="active"
        )
        session.add(asset)
        session.flush()
        return asset.id


def reservation_ids(database):
    with database() as session:
        return set(session.scalars(select(PartnerPlacementDelivery.delivery_id)))


@pytest.mark.parametrize("utc_offset", [0, -7], ids=["UTC", "previous-local-date"])
def test_pruning_keeps_two_day_boundary_and_durable_clickhouse_totals(
    database, partner_analytics, utc_offset
):
    asset = seed_asset(database)
    keep = set()
    with database.begin() as session:
        for age in (
            timedelta(),
            timedelta(days=1),
            timedelta(days=2),
            timedelta(days=2, microseconds=1),
            timedelta(days=30),
        ):
            delivery = uuid.uuid4()
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=delivery,
                    asset_id=asset,
                    issued_at=NOW - age,
                    impression_recorded_at=NOW - age,
                    click_recorded_at=NOW - age,
                )
            )
            if age <= timedelta(days=2):
                keep.add(delivery)
    partner_analytics.seed(asset, NOW.date(), 37, 11, baseline=True)
    before = partner_analytics.daily([asset])
    assert (
        prune_partner_delivery_history(
            database, now=NOW.astimezone(timezone(timedelta(hours=utc_offset)))
        )
        == 2
    )
    assert reservation_ids(database) == keep
    assert partner_analytics.daily([asset]) == before
    assert prune_partner_delivery_history(database, now=NOW) == 0


@pytest.mark.parametrize("batch_size", [3, 1000, 4000])
def test_each_pass_deletes_at_most_one_bounded_reservation_page(database, batch_size):
    asset, fresh = seed_asset(database), uuid.uuid4()
    with database.begin() as session:
        session.add_all(
            PartnerPlacementDelivery(
                delivery_id=uuid.uuid4(), asset_id=asset, issued_at=NOW - timedelta(days=4)
            )
            for _ in range(batch_size + 7)
        )
        session.add(PartnerPlacementDelivery(delivery_id=fresh, asset_id=asset, issued_at=NOW))
    assert prune_partner_delivery_history(database, now=NOW, batch_size=batch_size) == batch_size
    assert len(reservation_ids(database)) == 8
    while prune_partner_delivery_history(database, now=NOW, batch_size=batch_size):
        pass
    assert reservation_ids(database) == {fresh}


def test_materialized_batch_stays_bounded_under_nested_loop_query_plans(database):
    asset = seed_asset(database)
    with database.begin() as session:
        session.add_all(
            PartnerPlacementDelivery(
                delivery_id=uuid.uuid4(), asset_id=asset, issued_at=NOW - timedelta(days=4)
            )
            for _ in range(22)
        )

    class NestedLoopFactory:
        @staticmethod
        @contextmanager
        def begin():
            with database.begin() as session:
                for setting in ("enable_hashjoin", "enable_mergejoin", "enable_material"):
                    session.execute(text(f"SET LOCAL {setting}=off"))
                yield session

    assert prune_partner_delivery_history(NestedLoopFactory, now=NOW, batch_size=1) == 1
    assert len(reservation_ids(database)) == 21


def test_expired_receipts_stay_rejected_after_prune_and_live_replays_keep_one_event(
    database, client, monkeypatch, partner_analytics
):
    asset = seed_asset(database)
    now = datetime.now(UTC).replace(microsecond=0)
    signer = PartnerTrackingTokens(KEY, clock=lambda: (now - timedelta(days=4)).timestamp())
    old_token = signer.issue(asset)
    old_receipt = signer.verify(old_token)
    with database.begin() as session:
        claim = record_delivery_event(session, old_receipt, "impression")
    partner_analytics.publish_claim(old_receipt, "impression", claim)
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    valid_token = PartnerTrackingTokens(KEY).issue(asset)
    for _ in range(3):
        assert (
            client.post(
                "/v1/partner-tracking/events", json={"receipt": valid_token, "kind": "impression"}
            ).status_code
            == 204
        )
    before = partner_analytics.daily([asset])
    assert prune_partner_delivery_history(database, now=now) == 1
    assert partner_analytics.daily([asset]) == before
    assert (
        client.post(
            "/v1/partner-tracking/events", json={"receipt": old_token, "kind": "impression"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/partner-tracking/events", json={"receipt": valid_token, "kind": "impression"}
        ).status_code
        == 204
    )
    assert len(partner_analytics.events()) == 2
    assert len(reservation_ids(database)) == 1


def test_another_pruner_advisory_lane_returns_without_blocking(database):
    asset, delivery = seed_asset(database), uuid.uuid4()
    with database.begin() as session:
        session.add(
            PartnerPlacementDelivery(
                delivery_id=delivery, asset_id=asset, issued_at=NOW - timedelta(days=4)
            )
        )
    with database.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(832604284)))
        assert prune_partner_delivery_history(database, now=NOW) == 0
        assert reservation_ids(database) == {delivery}
    assert prune_partner_delivery_history(database, now=NOW) == 1


def test_locked_old_reservation_does_not_block_next_eligible_row(database):
    asset, locked, available = seed_asset(database), uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        for age, identity in ((8, locked), (4, available)):
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=identity, asset_id=asset, issued_at=NOW - timedelta(days=age)
                )
            )
    with ThreadPoolExecutor(max_workers=1) as executor, database.begin() as other:
        other.scalar(
            select(PartnerPlacementDelivery)
            .where(PartnerPlacementDelivery.delivery_id == locked)
            .with_for_update()
        )
        result = executor.submit(prune_partner_delivery_history, database, now=NOW, batch_size=1)
        assert result.result(timeout=3) == 1
        assert reservation_ids(database) == {locked}
    assert prune_partner_delivery_history(database, now=NOW, batch_size=1) == 1
    assert not reservation_ids(database)
