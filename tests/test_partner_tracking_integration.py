"""Real event deduplication, atomic counters and tenant-scoped portal reporting."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDeliveryEvent,
    PartnerTrackedDailyMetric,
)
from devfeed_core.partner_tracking import PartnerTrackingTokens, record_delivery_event
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from test_partner_portal import portal_client as portal_client
from test_partner_portal import seed

pytestmark = pytest.mark.integration
KEY = "disposable-partner-tracking-key-32-bytes"


def test_both_events_deduplicate_and_imports_cannot_overwrite_live_counts(
    client, portal_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    portal, identity = portal_client
    account_a, account_b, asset_a, asset_b = seed(database)
    signer = PartnerTrackingTokens(KEY)
    first = signer.issue(asset_a)
    for kind in ("click", "impression"):
        for _ in range(3):
            assert (
                client.post(
                    "/v1/partner-tracking/events", json={"kind": kind, "receipt": first}
                ).status_code
                == 204
            )
    assert (
        client.post(
            "/v1/partner-tracking/events",
            json={"kind": "impression", "receipt": signer.issue(asset_a)},
        ).status_code
        == 204
    )
    for kind in ("impression", "click"):
        assert (
            client.post(
                "/v1/partner-tracking/events", json={"kind": kind, "receipt": signer.issue(asset_b)}
            ).status_code
            == 204
        )
    path = f"/v1/partner/accounts/{account_a}/dashboard"
    before = portal.get(path).json()
    assert before["totals"] == {"impressions": 2, "clicks": 1, "ctr": 50.0, "measured_days": 1}
    assert before["assets"][0]["measured_days"] == 1
    assert portal.get(f"/v1/partner/accounts/{account_b}/dashboard").status_code == 404
    identity.roles = ["superuser"]
    metrics = f"/v1/partner/accounts/{account_a}/assets/{asset_a}/metrics"
    assert (
        portal.put(
            metrics, json={"day": str(datetime.now(UTC).date()), "impressions": 10, "clicks": 2}
        ).status_code
        == 204
    )
    combined = portal.get(path).json()
    assert combined["totals"] == {"impressions": 12, "clicks": 3, "ctr": 25.0, "measured_days": 1}
    assert combined["assets"][0]["measured_days"] == 1
    assert combined["trend"][0]["impressions"] == 12
    assert (
        portal.put(
            metrics, json={"day": str(datetime.now(UTC).date()), "impressions": 0, "clicks": 0}
        ).status_code
        == 204
    )
    assert portal.get(path).json()["totals"] == before["totals"]


@pytest.mark.parametrize("paused", ["account", "asset"])
def test_paused_delivery_is_rejected_without_measurements(client, database, monkeypatch, paused):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    account, _, asset, _ = seed(database)
    with database.begin() as session:
        session.get(
            PartnerAccount if paused == "account" else PartnerAsset,
            account if paused == "account" else asset,
        ).status = "paused"
    event = {"kind": "impression", "receipt": PartnerTrackingTokens(KEY).issue(asset)}
    assert client.post("/v1/partner-tracking/events", json=event).status_code == 403
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerDeliveryEvent)) == 0
        assert session.scalar(select(func.count()).select_from(PartnerTrackedDailyMetric)) == 0


def test_concurrent_duplicate_and_distinct_deliveries_increment_once(database):
    _, _, asset, _ = seed(database)
    signer = PartnerTrackingTokens(KEY)
    duplicate = signer.verify(signer.issue(asset))
    receipts = [duplicate] * 12 + [signer.verify(signer.issue(asset)) for _ in range(8)]

    def record(receipt):
        with database.begin() as session:
            return record_delivery_event(session, receipt, "impression")

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(record, receipts)) == 9
    with database() as session:
        metric = session.get(PartnerTrackedDailyMetric, (asset, duplicate.issued_at.date()))
        assert metric.impressions == 9 and metric.clicks == 0
        assert session.scalar(select(func.count()).select_from(PartnerDeliveryEvent)) == 9


def test_counter_failure_rolls_back_deduplication_claim(database, monkeypatch):
    _, _, asset, _ = seed(database)
    signer = PartnerTrackingTokens(KEY)
    receipt = signer.verify(signer.issue(asset, delivery_id=uuid.uuid4()))
    with pytest.raises(SQLAlchemyError), database.begin() as session:
        monkeypatch.setattr(session, "execute", lambda *_: (_ for _ in ()).throw(SQLAlchemyError()))
        record_delivery_event(session, receipt, "click")
    with database.begin() as session:
        assert record_delivery_event(session, receipt, "click")
    with database() as session:
        metric = session.get(PartnerTrackedDailyMetric, (asset, receipt.issued_at.date()))
        assert metric.clicks == 1 and metric.impressions == 0
