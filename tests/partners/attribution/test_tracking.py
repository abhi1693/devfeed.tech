"""Receipt expiry, replay protection, and bounded public event ingestion."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_api import partner_tracking as api
from devfeed_api.dependencies import get_session
from devfeed_api.main import create_app
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.config import Settings, get_settings
from devfeed_core.models import PartnerPlacementDelivery
from devfeed_core.partner_claim_backfill import LEGACY_CLAIM_AT
from devfeed_core.partner_tracking import (
    DeliveryClaim,
    DeliveryReceipt,
    InactivePartnerAsset,
    PartnerTrackingTokens,
    prune_partner_delivery_history,
    record_delivery_event,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.support.partner_matching import NOW

KEY = "disposable-partner-tracking-key-32-bytes"


def test_receipts_bind_one_delivery_to_one_asset_and_expire():
    now = [1800000000]
    signer = PartnerTrackingTokens(KEY, clock=lambda: now[0])
    asset, delivery = uuid.uuid4(), uuid.uuid4()
    token = signer.issue(asset, delivery_id=delivery)
    assert KEY not in token
    receipt = signer.verify(token)
    assert receipt.asset_id == asset and receipt.delivery_id == delivery
    assert receipt.issued_at == datetime.fromtimestamp(now[0], UTC)
    assert signer.verify(token.replace(asset.hex, uuid.uuid4().hex)) is None
    assert PartnerTrackingTokens("x" * 32, clock=lambda: now[0]).verify(token) is None
    now[0] -= 1
    assert signer.verify(token) is None
    now[0] += 3600
    assert signer.verify(token) is not None
    now[0] += 1
    assert signer.verify(token) is None


@pytest.mark.parametrize("token", ["", "invalid", "v1." + "0" * 300, "v2.a.b.c.d"])
def test_malformed_receipts_fail_closed(token):
    assert PartnerTrackingTokens(KEY).verify(token) is None


def test_tracking_is_disabled_by_default_and_rejects_short_keys():
    assert Settings(_env_file=None, partner_tracking_key="").partner_tracking_key is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, partner_tracking_key="short")
    with pytest.raises(ValueError):
        PartnerTrackingTokens("short")


@pytest.mark.parametrize("kind", ["impression", "click"])
def test_event_uses_operational_claim_and_no_analytics_counter(kind):
    session = Mock()
    receipt = DeliveryReceipt(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC))
    account_id = uuid.uuid4()
    fences = [
        account_id,
        SimpleNamespace(status="active"),
        SimpleNamespace(automatic=False, status="active"),
    ]
    now = datetime.now(UTC)
    session.scalar.side_effect = [*fences, now]
    claim = record_delivery_event(session, receipt, kind)
    assert claim and claim.recorded_at == now
    statement = session.scalar.call_args.args[0].compile(dialect=dialect())
    assert "UPDATE partner_placement_deliveries" in str(statement)
    assert "partner_tracked_daily_metrics" not in str(statement)
    session.scalar.side_effect = [*fences, None]
    session.get.return_value = SimpleNamespace(
        asset_id=receipt.asset_id, impression_recorded_at=now, click_recorded_at=now
    )
    claim = record_delivery_event(session, receipt, kind)
    assert not claim and claim.recorded_at == now
    session.scalar.side_effect = [None]
    with pytest.raises(InactivePartnerAsset):
        record_delivery_event(session, receipt, kind)


@pytest.fixture
def tracking_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    session = Mock()
    monkeypatch.setattr(api, "consume_rate_limits", Mock(return_value=0))
    monkeypatch.setattr(api, "consume_reserved_rate_limits", Mock(return_value=0))
    monkeypatch.setattr(api, "get_rate_limit_redis", Mock())
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    class Analytics:
        available = True
        ready = True
        rows = []

        async def query(self, _statement, _params):
            return [{"source": "partner_delivery_events", "complete": int(self.ready)}]

        async def insert(self, _table, rows):
            self.rows.extend(rows)
            if not self.available:
                raise AnalyticsUnavailable("offline")

    app.state.analytics_client = Analytics()
    yield TestClient(app), session


@pytest.mark.parametrize(
    "mode,status",
    [
        ("ok", 204),
        ("inactive", 403),
        ("database", 503),
        ("redis", 503),
        ("limited", 429),
        ("shared-redis", 503),
        ("shared-limited", 429),
        ("commit", 503),
    ],
)
def test_event_failures_do_not_commit_partial_measurements(
    tracking_client, monkeypatch, mode, status
):
    client, session = tracking_client
    record = Mock(return_value=DeliveryClaim(datetime.now(UTC), True))
    monkeypatch.setattr(api, "record_delivery_event", record)
    if mode == "inactive":
        record.side_effect = InactivePartnerAsset()
    elif mode == "database":
        record.side_effect = SQLAlchemyError()
    elif mode == "redis":
        api.consume_rate_limits.side_effect = RedisError()
    elif mode == "limited":
        api.consume_rate_limits.return_value = 60
    elif mode == "shared-redis":
        api.consume_reserved_rate_limits.side_effect = RedisError()
    elif mode == "shared-limited":
        api.consume_reserved_rate_limits.return_value = 60
    elif mode == "commit":
        session.commit.side_effect = SQLAlchemyError()
    response = client.post(
        "/v1/partner-tracking/events",
        json={"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())},
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert session.commit.call_count == int(mode in {"ok", "commit"})
    assert session.rollback.call_count == int(
        mode in {"database", "shared-redis", "shared-limited", "commit"}
    )
    if mode in {"redis", "limited"}:
        record.assert_not_called()
    if mode in {"limited", "shared-limited"}:
        assert response.headers["retry-after"] == "60"


def test_duplicate_events_charge_only_the_callers_abuse_budget(tracking_client, monkeypatch):
    client, session = tracking_client
    record = Mock(return_value=DeliveryClaim(datetime.now(UTC), False))
    monkeypatch.setattr(api, "record_delivery_event", record)
    response = client.post(
        "/v1/partner-tracking/events",
        json={"kind": "impression", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())},
    )
    assert response.status_code == 204
    budgets = api.consume_rate_limits.call_args.args[1]
    assert len(budgets) == 1 and ":caller:" in budgets[0].key
    assert budgets[0].limit == 180
    api.consume_reserved_rate_limits.assert_not_called()
    session.commit.assert_called_once_with()


def test_impression_and_click_abuse_budgets_are_independent(tracking_client, monkeypatch):
    client, _ = tracking_client
    monkeypatch.setattr(
        api, "record_delivery_event", Mock(return_value=DeliveryClaim(datetime.now(UTC), False))
    )
    token = PartnerTrackingTokens(KEY).issue(uuid.uuid4())
    caller_keys = []
    for kind in ("impression", "click"):
        assert (
            client.post(
                "/v1/partner-tracking/events", json={"kind": kind, "receipt": token}
            ).status_code
            == 204
        )
        caller_keys.append(api.consume_rate_limits.call_args.args[1][0].key)
    assert caller_keys[0] != caller_keys[1]
    assert caller_keys[0].endswith(":impression:minute")
    assert caller_keys[1].endswith(":click:minute")


def test_retry_after_shared_redis_failure_preserves_the_quota_reservation_identity(
    tracking_client, monkeypatch
):
    client, session = tracking_client
    monkeypatch.setattr(
        api, "record_delivery_event", Mock(return_value=DeliveryClaim(datetime.now(UTC), True))
    )
    api.consume_reserved_rate_limits.side_effect = [RedisError(), 0]
    event = {"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())}
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 503
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 204
    attempts = api.consume_reserved_rate_limits.call_args_list
    assert attempts[0].kwargs["reservation_key"] == attempts[1].kwargs["reservation_key"]
    session.rollback.assert_called_once_with()
    session.commit.assert_called_once_with()


def test_retry_after_clickhouse_failure_reuses_the_committed_claim(tracking_client, monkeypatch):
    client, session = tracking_client
    observed = datetime.now(UTC)
    record = Mock(side_effect=[DeliveryClaim(observed, True), DeliveryClaim(observed, False)])
    monkeypatch.setattr(api, "record_delivery_event", record)
    analytics = client.app.state.analytics_client
    analytics.available = False
    event = {"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())}
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 503
    analytics.available = True
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 204
    assert analytics.rows[0] == analytics.rows[1]
    assert session.commit.call_count == 2


def test_historical_claims_do_not_duplicate_imported_baseline(tracking_client, monkeypatch):
    client, _ = tracking_client
    monkeypatch.setattr(
        api, "record_delivery_event", Mock(return_value=DeliveryClaim(LEGACY_CLAIM_AT, False))
    )
    event = {"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())}
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 204
    assert client.app.state.analytics_client.rows == []


def test_claim_import_readiness_is_required_before_writing(tracking_client, monkeypatch):
    client, session = tracking_client
    client.app.state.analytics_client.ready = False
    record = Mock()
    monkeypatch.setattr(api, "record_delivery_event", record)
    event = {"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())}
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 503
    record.assert_not_called()
    session.commit.assert_not_called()


def test_unproven_events_and_oversized_bodies_never_reach_storage(tracking_client, monkeypatch):
    client, session = tracking_client
    record = Mock()
    monkeypatch.setattr(api, "record_delivery_event", record)
    for kind in ("impression", "click"):
        assert (
            client.post(
                "/v1/partner-tracking/events", json={"kind": kind, "receipt": "forged"}
            ).status_code
            == 403
        )
    assert client.post("/v1/partner-tracking/events", content="x" * 2049).status_code == 413
    monkeypatch.setattr(get_settings(), "partner_tracking_key", None)
    assert (
        client.post(
            "/v1/partner-tracking/events",
            json={"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())},
        ).status_code
        == 403
    )
    record.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("batch", [0, 4001])
def test_receipt_cleanup_rejects_unbounded_batches_before_opening_storage(batch):
    factory = Mock()
    with pytest.raises(ValueError, match="cleanup batch must be 1..4000"):
        prune_partner_delivery_history(factory, batch_size=batch, now=NOW)
    factory.begin.assert_not_called()


def test_unadmitted_cleanup_does_not_issue_any_ledger_writes():
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = False
    factory.begin.return_value = nullcontext(session)
    assert prune_partner_delivery_history(factory, now=NOW, batch_size=3) == 0
    session.execute.assert_not_called()


@pytest.mark.parametrize("count", [0, 3])
def test_cleanup_prunes_only_expired_operational_claims(count):
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = True
    session.execute.return_value = SimpleNamespace(rowcount=count)
    factory.begin.return_value = nullcontext(session)
    assert prune_partner_delivery_history(factory, now=NOW, batch_size=3) == count
    assert session.execute.call_args.args[0].table.name == PartnerPlacementDelivery.__tablename__
    session.add.assert_not_called()


def test_cleanup_propagates_repository_failure():
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = True
    error = SQLAlchemyError("receipt claims unavailable")
    session.execute.side_effect = error
    factory.begin.return_value = nullcontext(session)
    with pytest.raises(SQLAlchemyError) as failure:
        prune_partner_delivery_history(factory, now=NOW, batch_size=3)
    assert failure.value is error
    session.execute.assert_called_once()
