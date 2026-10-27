"""Operational receipt deduplication and direct ClickHouse tenant reporting."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import sleep

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerPlacementDelivery,
)
from devfeed_core.partner_tracking import (
    BusyPartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from tests.support.partner_portal import admin_portal_client as admin_portal_client
from tests.support.partner_portal import portal_client as portal_client
from tests.support.partner_portal import seed

pytestmark = pytest.mark.integration
KEY = "disposable-partner-tracking-key-32-bytes"


def test_both_events_deduplicate_and_imports_cannot_overwrite_live_counts(
    client, portal_client, admin_portal_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    portal, identity = portal_client
    admin, _ = admin_portal_client
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
    metrics = f"/v1/admin/partner-accounts/{account_a}/assets/{asset_a}/metrics"
    assert (
        admin.put(
            metrics, json={"day": str(datetime.now(UTC).date()), "impressions": 10, "clicks": 2}
        ).status_code
        == 204
    )
    combined = portal.get(path).json()
    assert combined["totals"] == {"impressions": 12, "clicks": 3, "ctr": 25.0, "measured_days": 1}
    assert combined["assets"][0]["measured_days"] == 1
    assert combined["trend"][0]["impressions"] == 12
    assert (
        admin.put(
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
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPlacementDelivery)
                .where(
                    PartnerPlacementDelivery.impression_recorded_at.is_not(None)
                    | PartnerPlacementDelivery.click_recorded_at.is_not(None)
                )
            )
            == 0
        )


def test_concurrent_duplicate_and_distinct_deliveries_increment_once(database):
    _, _, asset, _ = seed(database)
    signer = PartnerTrackingTokens(KEY)
    duplicate = signer.verify(signer.issue(asset))
    receipts = [duplicate] * 12 + [signer.verify(signer.issue(asset)) for _ in range(8)]

    def record(receipt):
        # Busy attribution produces a retriable 503 in HTTP. Each retry starts a
        # fresh transaction, retaining the same signed delivery and event identity.
        for attempt in range(20):
            try:
                with database.begin() as session:
                    return bool(record_delivery_event(session, receipt, "impression"))
            except BusyPartnerAsset:
                sleep(0.005 * (attempt + 1))
        raise AssertionError("Partner attribution remained busy after bounded retries")

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(record, receipts)) == 9
    with database() as session:
        claims = session.scalars(select(PartnerPlacementDelivery)).all()
        assert len(claims) == 9
        assert all(row.impression_recorded_at is not None for row in claims)
        assert all(row.click_recorded_at is None for row in claims)


def test_claim_failure_rolls_back_deduplication(database, monkeypatch):
    _, _, asset, _ = seed(database)
    signer = PartnerTrackingTokens(KEY)
    receipt = signer.verify(signer.issue(asset, delivery_id=uuid.uuid4()))
    with pytest.raises(SQLAlchemyError), database.begin() as session:
        monkeypatch.setattr(session, "execute", lambda *_: (_ for _ in ()).throw(SQLAlchemyError()))
        record_delivery_event(session, receipt, "click")
    with database.begin() as session:
        assert record_delivery_event(session, receipt, "click")
    with database() as session:
        claim = session.get(PartnerPlacementDelivery, receipt.delivery_id)
        assert claim.click_recorded_at is not None
        assert claim.impression_recorded_at is None
