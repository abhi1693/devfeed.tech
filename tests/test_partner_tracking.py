"""Receipt expiry, replay protection, and bounded public event ingestion."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_api import partner_tracking as api
from devfeed_api.dependencies import get_session
from devfeed_api.main import create_app
from devfeed_core.config import Settings, get_settings
from devfeed_core.partner_tracking import (
    DeliveryReceipt,
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import SQLAlchemyError

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
def test_event_and_counter_use_atomic_conflicts_without_imported_totals(kind):
    session = Mock()
    receipt = DeliveryReceipt(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC))
    account_id = uuid.uuid4()
    fences = [
        account_id,
        SimpleNamespace(status="active"),
        SimpleNamespace(automatic=False, status="active"),
    ]
    session.scalar.side_effect = [*fences, receipt.delivery_id]
    assert record_delivery_event(session, receipt, kind)
    statement = session.execute.call_args.args[0].compile(dialect=dialect())
    assert "ON CONFLICT" in str(statement)
    assert "partner_tracked_daily_metrics" in str(statement)
    assert "partner_daily_metrics" not in str(statement)
    assert statement.params["impressions"] == int(kind == "impression")
    assert statement.params["clicks"] == int(kind == "click")
    session.scalar.side_effect = [*fences, None]
    session.execute.reset_mock()
    assert not record_delivery_event(session, receipt, kind)
    session.execute.assert_not_called()
    session.scalar.side_effect = [None]
    with pytest.raises(InactivePartnerAsset):
        record_delivery_event(session, receipt, kind)
    session.execute.assert_not_called()


@pytest.fixture
def tracking_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    session = Mock()
    monkeypatch.setattr(api, "consume_rate_limits", Mock(return_value=0))
    monkeypatch.setattr(api, "consume_reserved_rate_limits", Mock(return_value=0))
    monkeypatch.setattr(api, "get_rate_limit_redis", Mock())
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
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
    record = Mock(return_value=True)
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
    record = Mock(return_value=False)
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
    monkeypatch.setattr(api, "record_delivery_event", Mock(return_value=False))
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
    monkeypatch.setattr(api, "record_delivery_event", Mock(return_value=True))
    api.consume_reserved_rate_limits.side_effect = [RedisError(), 0]
    event = {"kind": "click", "receipt": PartnerTrackingTokens(KEY).issue(uuid.uuid4())}
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 503
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 204
    attempts = api.consume_reserved_rate_limits.call_args_list
    assert attempts[0].kwargs["reservation_key"] == attempts[1].kwargs["reservation_key"]
    session.rollback.assert_called_once_with()
    session.commit.assert_called_once_with()


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
