"""Bounded delivery-history pruning preserves replay fences and durable reporting totals."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

import pytest
from devfeed_api import partner_tracking as tracking_api
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDailyMetric,
    PartnerDeliveryEvent,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerTrackedDailyMetric,
)
from devfeed_core.partner_tracking import (
    PartnerTrackingTokens,
    prune_partner_delivery_history,
    record_delivery_event,
)
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import func, select, text

pytestmark = pytest.mark.integration
KEY = "disposable-partner-retention-tracking-key-32-bytes"
NOW = datetime(2026, 10, 13, 0, 30, tzinfo=UTC)


def seed_assets(database):
    with database.begin() as session:
        account = PartnerAccount(name="Reporting partner", tier="gold", status="active")
        session.add(account)
        session.flush()
        assets = [
            PartnerAsset(account_id=account.id, name=name, kind="ad", status="active")
            for name in ("First asset", "Second asset")
        ]
        session.add_all(assets)
        session.flush()
        return [asset.id for asset in assets]


def event_keys(database):
    with database() as session:
        return set(
            session.execute(
                select(
                    PartnerDeliveryEvent.asset_id,
                    PartnerDeliveryEvent.delivery_id,
                    PartnerDeliveryEvent.kind,
                )
            ).all()
        )


def reservation_ids(database):
    with database() as session:
        return set(session.scalars(select(PartnerPlacementDelivery.delivery_id)))


def metric_snapshot(database):
    with database() as session:
        return {
            model.__tablename__: session.execute(
                select(
                    model.asset_id, model.day, model.impressions, model.clicks, model.updated_at
                ).order_by(model.asset_id, model.day)
            ).all()
            for model in (PartnerDailyMetric, PartnerTrackedDailyMetric)
        }


@pytest.mark.parametrize("utc_offset", [0, -7], ids=["UTC", "previous-local-date"])
def test_pruning_preserves_three_utc_dates_reservation_boundary_and_all_aggregates(
    database, utc_offset
):
    asset, _ = seed_assets(database)
    fresh_events, fresh_reservations = set(), set()
    with database.begin() as session:
        for age in (0, 1, 2, 3, 30):
            day = (NOW - timedelta(days=age)).date()
            for kind in ("impression", "click"):
                delivery = uuid.uuid4()
                session.add(
                    PartnerDeliveryEvent(asset_id=asset, delivery_id=delivery, kind=kind, day=day)
                )
                if age <= 2:
                    fresh_events.add((asset, delivery, kind))
            for model in (PartnerDailyMetric, PartnerTrackedDailyMetric):
                session.add(
                    model(asset_id=asset, day=day, impressions=37, clicks=11, updated_at=NOW)
                )
        for age in (
            timedelta(),
            timedelta(days=1),
            timedelta(days=2),
            timedelta(days=2, microseconds=1),
            timedelta(days=30),
        ):
            delivery = uuid.uuid4()
            session.add(
                PartnerPlacementDelivery(delivery_id=delivery, asset_id=asset, issued_at=NOW - age)
            )
            if age <= timedelta(days=2):
                fresh_reservations.add(delivery)
    before = metric_snapshot(database)
    local_now = NOW.astimezone(timezone(timedelta(hours=utc_offset)))

    assert prune_partner_delivery_history(database, now=local_now) == 6
    assert event_keys(database) == fresh_events
    assert reservation_ids(database) == fresh_reservations
    assert metric_snapshot(database) == before
    assert prune_partner_delivery_history(database, now=local_now) == 0


@pytest.mark.parametrize("batch_size", [3, 1000, 4000], ids=["small", "1000", "default"])
def test_each_pass_has_independent_bounded_budgets_without_any_catalog_products(
    database, batch_size
):
    asset, _ = seed_assets(database)
    fresh_delivery = uuid.uuid4()
    with database.begin() as session:
        session.add_all(
            PartnerDeliveryEvent(
                asset_id=asset,
                delivery_id=uuid.uuid4(),
                kind="impression",
                day=(NOW - timedelta(days=4)).date(),
            )
            for _ in range(batch_size + 5)
        )
        session.add_all(
            PartnerPlacementDelivery(
                delivery_id=uuid.uuid4(), asset_id=asset, issued_at=NOW - timedelta(days=4)
            )
            for _ in range(batch_size + 7)
        )
        session.add(
            PartnerDeliveryEvent(
                asset_id=asset, delivery_id=fresh_delivery, kind="click", day=NOW.date()
            )
        )
        session.add(
            PartnerPlacementDelivery(delivery_id=fresh_delivery, asset_id=asset, issued_at=NOW)
        )
    options = {} if batch_size == 4000 else {"batch_size": batch_size}
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerProduct)) == 0

    assert prune_partner_delivery_history(database, now=NOW, **options) == 2 * batch_size
    assert len(event_keys(database)) == 6
    assert len(reservation_ids(database)) == 8
    for _ in range(5):
        if prune_partner_delivery_history(database, now=NOW, **options) == 0:
            break
    else:
        pytest.fail("Bounded pruning did not exhaust the expired history")
    assert event_keys(database) == {(asset, fresh_delivery, "click")}
    assert reservation_ids(database) == {fresh_delivery}


def test_oldest_first_deletion_matches_the_complete_event_primary_key(database):
    first, second = seed_assets(database)
    shared_delivery = uuid.uuid4()
    old_keys = [
        (first, shared_delivery, "impression"),
        (first, uuid.uuid4(), "click"),
        (second, uuid.uuid4(), "impression"),
        (second, uuid.uuid4(), "click"),
    ]
    fresh_keys = {
        (first, shared_delivery, "click"),
        (second, shared_delivery, "impression"),
        (second, old_keys[1][1], "click"),
        (first, old_keys[2][1], "impression"),
    }
    with database.begin() as session:
        for index, (asset, delivery, kind) in enumerate(old_keys):
            session.add(
                PartnerDeliveryEvent(
                    asset_id=asset,
                    delivery_id=delivery,
                    kind=kind,
                    day=(NOW - timedelta(days=8 - index)).date(),
                )
            )
            session.add(
                PartnerPlacementDelivery(
                    asset_id=asset,
                    delivery_id=delivery,
                    issued_at=NOW - timedelta(days=8 - index),
                )
            )
        session.add_all(
            PartnerDeliveryEvent(asset_id=asset, delivery_id=delivery, kind=kind, day=NOW.date())
            for asset, delivery, kind in fresh_keys
        )

    assert prune_partner_delivery_history(database, now=NOW, batch_size=3) == 6
    assert event_keys(database) == fresh_keys | {old_keys[-1]}
    assert reservation_ids(database) == {old_keys[-1][1]}
    assert prune_partner_delivery_history(database, now=NOW, batch_size=3) == 2
    assert event_keys(database) == fresh_keys
    assert not reservation_ids(database)


@pytest.mark.parametrize("expired_count", [22, 1001], ids=["stale-small-table", "large-backlog"])
def test_batch_limits_survive_nested_loop_query_plans(database, expired_count):
    asset, _ = seed_assets(database)
    fresh = uuid.uuid4()
    with database.begin() as session:
        for index in range(1, expired_count + 1):
            delivery = uuid.UUID(int=index)
            session.add(
                PartnerDeliveryEvent(
                    asset_id=asset,
                    delivery_id=delivery,
                    kind="impression",
                    day=(NOW - timedelta(days=4)).date(),
                )
            )
            session.add(
                PartnerPlacementDelivery(
                    asset_id=asset, delivery_id=delivery, issued_at=NOW - timedelta(days=4)
                )
            )
            if index == 1:
                session.flush()
                # Growth after ANALYZE can favor a semi-join that rescans its inner input.
                session.execute(text("ANALYZE partner_delivery_events"))
                session.execute(text("ANALYZE partner_placement_deliveries"))
        session.add(
            PartnerDeliveryEvent(asset_id=asset, delivery_id=fresh, kind="click", day=NOW.date())
        )
        session.add(PartnerPlacementDelivery(asset_id=asset, delivery_id=fresh, issued_at=NOW))

    class NestedLoopFactory:
        @staticmethod
        @contextmanager
        def begin():
            with database.begin() as session:
                # Repeated nested-loop scans must not select another batch of rows.
                for setting in ("enable_hashjoin", "enable_mergejoin", "enable_material"):
                    session.execute(text(f"SET LOCAL {setting} = off"))
                yield session

    for pass_number in (1, 2):
        assert prune_partner_delivery_history(NestedLoopFactory, now=NOW, batch_size=1) == 2
        remaining_events = event_keys(database)
        remaining_reservations = reservation_ids(database)
        assert len(remaining_events) == expired_count + 1 - pass_number
        assert len(remaining_reservations) == expired_count + 1 - pass_number
        assert (asset, fresh, "click") in remaining_events
        assert fresh in remaining_reservations


def test_expired_receipts_stay_rejected_after_purge_and_valid_receipts_remain_deduplicated(
    database, client, monkeypatch
):
    asset, _ = seed_assets(database)
    now = datetime.now(UTC).replace(microsecond=0)
    old_signer = PartnerTrackingTokens(KEY, clock=lambda: (now - timedelta(days=4)).timestamp())
    expired_token = old_signer.issue(asset)
    expired_receipt = old_signer.verify(expired_token)
    assert expired_receipt is not None
    with database.begin() as session:
        assert record_delivery_event(session, expired_receipt, "impression")
        assert record_delivery_event(session, expired_receipt, "click")
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    valid_token = PartnerTrackingTokens(KEY).issue(asset)
    valid_receipt = PartnerTrackingTokens(KEY).verify(valid_token)
    assert valid_receipt is not None
    with Redis.from_url(get_settings().redis_url) as redis:
        monkeypatch.setattr(tracking_api, "get_rate_limit_redis", lambda: redis)
        event = {"receipt": valid_token, "kind": "impression"}
        assert client.post("/v1/partner-tracking/events", json=event).status_code == 204
        before = metric_snapshot(database)

        assert prune_partner_delivery_history(database, now=now) == 2
        assert metric_snapshot(database) == before
        for kind in ("impression", "click"):
            rejected = client.post(
                "/v1/partner-tracking/events", json={"receipt": expired_token, "kind": kind}
            )
            assert rejected.status_code == 403
            assert rejected.json() == {"detail": "Invalid partner delivery receipt"}
        for _ in range(3):
            assert client.post("/v1/partner-tracking/events", json=event).status_code == 204
        for _ in range(2):
            assert (
                client.post(
                    "/v1/partner-tracking/events", json={"receipt": valid_token, "kind": "click"}
                ).status_code
                == 204
            )
    assert event_keys(database) == {
        (asset, valid_receipt.delivery_id, "impression"),
        (asset, valid_receipt.delivery_id, "click"),
    }
    with database() as session:
        old = session.get(PartnerTrackedDailyMetric, (asset, expired_receipt.issued_at.date()))
        current = session.get(PartnerTrackedDailyMetric, (asset, valid_receipt.issued_at.date()))
        assert (old.impressions, old.clicks) == (1, 1)
        assert (current.impressions, current.clicks) == (1, 1)


def test_another_pruner_holds_the_advisory_lane_without_blocking(database):
    asset, _ = seed_assets(database)
    delivery = uuid.uuid4()
    with database.begin() as session:
        session.add(
            PartnerDeliveryEvent(
                asset_id=asset,
                delivery_id=delivery,
                kind="click",
                day=(NOW - timedelta(days=4)).date(),
            )
        )
    with database.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(832604284)))
        assert prune_partner_delivery_history(database, now=NOW) == 0
        assert event_keys(database) == {(asset, delivery, "click")}
    assert prune_partner_delivery_history(database, now=NOW) == 1
    assert not event_keys(database)


def test_locked_old_rows_do_not_block_the_next_eligible_event_or_reservation(database):
    asset, _ = seed_assets(database)
    locked, available = uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        for age, delivery in ((8, locked), (4, available)):
            session.add(
                PartnerDeliveryEvent(
                    asset_id=asset,
                    delivery_id=delivery,
                    kind="impression",
                    day=(NOW - timedelta(days=age)).date(),
                )
            )
            session.add(
                PartnerPlacementDelivery(
                    asset_id=asset, delivery_id=delivery, issued_at=NOW - timedelta(days=age)
                )
            )
    with ThreadPoolExecutor(max_workers=1) as executor, database.begin() as other:
        other.scalar(
            select(PartnerDeliveryEvent)
            .where(PartnerDeliveryEvent.delivery_id == locked)
            .with_for_update()
        )
        other.scalar(
            select(PartnerPlacementDelivery)
            .where(PartnerPlacementDelivery.delivery_id == locked)
            .with_for_update()
        )
        pending = executor.submit(prune_partner_delivery_history, database, now=NOW, batch_size=1)
        assert pending.result(timeout=3) == 2
        assert event_keys(database) == {(asset, locked, "impression")}
        assert reservation_ids(database) == {locked}
    assert prune_partner_delivery_history(database, now=NOW, batch_size=1) == 2
    assert not event_keys(database)
    assert not reservation_ids(database)
