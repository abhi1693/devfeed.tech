"""Every native format counts real issued clicks once and rejects fabricated attribution."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import sleep

import pytest
from devfeed_api.main import create_app
from devfeed_core.models import (
    PartnerAsset,
    PartnerCampaign,
    PartnerDeliveryEvent,
    PartnerMembership,
    PartnerPlacementDelivery,
    PartnerTrackedDailyMetric,
    utcnow,
)
from devfeed_core.partner_tracking import PartnerTrackingTokens
from devfeed_user_api.auth import UserIdentity, require_browser_user
from devfeed_user_api.main import create_app as user_app
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.partners.placements.surfaces.test_authenticated_placements import (
    configure_campaign,
    page_query,
)
from tests.partners.placements.surfaces.test_feed_placements import feed_catalog as feed_catalog
from tests.support.partner_placements import KEY
from tests.support.partner_placements import catalog as catalog
from tests.support.partner_portal import admin_portal_client as admin_portal_client
from tests.support.partner_portal import portal_client as portal_client

pytestmark = pytest.mark.integration
FORMATS = ["feed", "engagement", "header_sponsorship", "sponsored_content"]
EVENTS = "/v1/partner-tracking/events"
PUBLIC_QUERY = "/v1/partner-placements/query"


def configure(database, catalog, ad_format):
    surface = "header" if ad_format == "header_sponsorship" else "feed"
    configure_campaign(database, catalog, ad_format, surface)
    return page_query(catalog, ad_format, surface, "collection").model_dump(mode="json")


def issue(client, payload, *, path=PUBLIC_QUERY):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["items"] if item["sponsored"])


def assert_counts(database, token, *, clicks, impressions):
    receipt = PartnerTrackingTokens(KEY).verify(token)
    assert receipt is not None
    with database() as session:
        metric = session.get(
            PartnerTrackedDailyMetric, (receipt.asset_id, receipt.issued_at.date())
        )
        if not clicks and not impressions:
            assert metric is None
        else:
            assert metric is not None
            assert (metric.clicks, metric.impressions) == (clicks, impressions)
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerDeliveryEvent)
                .where(PartnerDeliveryEvent.asset_id == receipt.asset_id)
            )
            == clicks + impressions
        )


@pytest.mark.parametrize("ad_format", FORMATS)
@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("click_first", [False, True])
def test_every_format_counts_an_issued_click_once_in_api_storage_and_reporting(
    client,
    database,
    feed_catalog,
    admin_portal_client,
    portal_client,
    ad_format,
    authenticated,
    click_first,
):
    payload = configure(database, feed_catalog, ad_format)
    app = user_app()
    # Session and CSRF validation have their own real HTTP boundary tests. Here a
    # trusted identity exercises the actual private issuer and real SQL selection.
    app.dependency_overrides[require_browser_user] = lambda: UserIdentity(
        user_id=str(feed_catalog["user"]),
        subject="reader",
        issuer="https://identity.example",
        organization_id="org",
        expires_at=2**31,
        csrf_token="trusted-test-session",
    )
    with TestClient(app) as private:
        item = issue(
            private if authenticated else client,
            payload,
            path="/v1/user/partner-placements/query" if authenticated else PUBLIC_QUERY,
        )
    token = item["receipt"]
    assert item["ad_format"] == ad_format
    for kind in ("click", "impression") if click_first else ("impression", "click"):
        for _ in range(3):
            response = client.post(EVENTS, json={"receipt": token, "kind": kind})
            assert response.status_code == 204, response.text
    assert_counts(database, token, clicks=1, impressions=1)
    admin, _ = admin_portal_client
    report = admin.get(
        f"/v1/admin/partner-accounts/{feed_catalog['paid']}/dashboard",
        params={"ad_format": ad_format},
    )
    assert report.status_code == 200, report.text
    assert report.json()["totals"] == {
        "impressions": 1,
        "clicks": 1,
        "ctr": 100.0,
        "measured_days": 1,
    }
    assert {row["ad_format"] for row in report.json()["assets"]} == {ad_format}
    assert report.json()["format_totals"] == [{"ad_format": ad_format, **report.json()["totals"]}]
    with database.begin() as session:
        asset = session.get(PartnerAsset, uuid.UUID(item["asset_id"]))
        assert asset.account_id == feed_catalog["paid"]
        assert asset.product_id == feed_catalog["paid_product"]
        assert asset.campaign_id == feed_catalog["campaign"]
        assert asset.ad_format == ad_format
        session.add(
            PartnerMembership(
                account_id=feed_catalog["paid"],
                issuer="https://identity.example",
                subject="alice",
            )
        )
    portal, _ = portal_client
    own = portal.get(
        f"/v1/partner/accounts/{feed_catalog['paid']}/dashboard",
        params={"ad_format": ad_format},
    )
    assert own.status_code == 200, own.text
    assert own.json()["totals"] == report.json()["totals"]
    assert own.json()["format_totals"] == report.json()["format_totals"]
    assert portal.get(f"/v1/partner/accounts/{feed_catalog['free']}/dashboard").status_code == 404


@pytest.mark.parametrize("ad_format", FORMATS)
@pytest.mark.parametrize(
    "attack",
    ["asset", "delivery", "timestamp", "signature", "unissued", "wrong-issue-time", "owner"],
)
def test_each_format_rejects_tampered_or_unissued_clicks_without_incrementing(
    client, database, feed_catalog, ad_format, attack
):
    item = issue(client, configure(database, feed_catalog, ad_format))
    token = item["receipt"]
    signer = PartnerTrackingTokens(KEY)
    receipt = signer.verify(token)
    assert receipt is not None
    parts = token.split(".")
    if attack == "asset":
        parts[1] = uuid.uuid4().hex
    elif attack == "delivery":
        parts[2] = uuid.uuid4().hex
    elif attack == "timestamp":
        parts[3] = str(int(parts[3]) - 1)
    elif attack == "signature":
        parts[4] = ("1" if parts[4][0] == "0" else "0") + parts[4][1:]
    elif attack == "unissued":
        # Even a server-signed receipt cannot invent a trusted issuance row.
        parts = signer.issue(receipt.asset_id).split(".")
    elif attack == "owner":
        with database() as session:
            other_asset = session.scalar(
                select(PartnerAsset.id).where(
                    PartnerAsset.account_id == feed_catalog["free"], PartnerAsset.automatic
                )
            )
            assert other_asset is not None
        parts = signer.issue(other_asset, delivery_id=receipt.delivery_id).split(".")
    else:
        parts = (
            PartnerTrackingTokens(KEY, clock=lambda: receipt.issued_at.timestamp() - 1)
            .issue(receipt.asset_id, delivery_id=receipt.delivery_id)
            .split(".")
        )
    response = client.post(EVENTS, json={"receipt": ".".join(parts), "kind": "click"})
    assert response.status_code == 403, response.text
    assert_counts(database, token, clicks=0, impressions=0)
    # Rejection does not consume the genuine event identity.
    assert client.post(EVENTS, json={"receipt": token, "kind": "click"}).status_code == 204
    assert_counts(database, token, clicks=1, impressions=0)


@pytest.mark.parametrize("ad_format", FORMATS)
def test_exhausted_capacity_stops_new_delivery_but_preserves_a_genuine_issued_click(
    client, database, feed_catalog, ad_format
):
    payload = configure(database, feed_catalog, ad_format)
    with database.begin() as session:
        session.get(PartnerCampaign, feed_catalog["campaign"]).daily_impression_limit = 1
    item = issue(client, payload)
    later = client.post(PUBLIC_QUERY, json=payload)
    assert later.status_code == 200, later.text
    assert not any(row["sponsored"] for row in later.json()["items"])
    for _ in range(3):
        assert (
            client.post(EVENTS, json={"receipt": item["receipt"], "kind": "click"}).status_code
            == 204
        )
    assert_counts(database, item["receipt"], clicks=1, impressions=0)


def test_ordinary_noncampaign_feed_clicks_use_the_same_durable_deduplication(
    client, database, feed_catalog
):
    payload = configure(database, feed_catalog, "feed")
    response = client.post(PUBLIC_QUERY, json=payload)
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["items"] if not row["sponsored"])
    assert item["campaign_id"] is None
    for _ in range(3):
        assert (
            client.post(EVENTS, json={"receipt": item["receipt"], "kind": "click"}).status_code
            == 204
        )
    assert_counts(database, item["receipt"], clicks=1, impressions=0)


@pytest.mark.parametrize("ad_format", FORMATS)
def test_each_format_rejects_expired_click_receipts_before_storage(
    client, database, feed_catalog, ad_format
):
    item = issue(client, configure(database, feed_catalog, ad_format))
    receipt = PartnerTrackingTokens(KEY).verify(item["receipt"])
    assert receipt is not None
    issued = utcnow() - timedelta(hours=1, seconds=1)
    with database.begin() as session:
        session.get(PartnerPlacementDelivery, receipt.delivery_id).issued_at = issued
    expired = PartnerTrackingTokens(KEY, clock=lambda: issued.timestamp()).issue(
        receipt.asset_id, delivery_id=receipt.delivery_id
    )
    assert client.post(EVENTS, json={"receipt": expired, "kind": "click"}).status_code == 403
    assert_counts(database, item["receipt"], clicks=0, impressions=0)


@pytest.mark.parametrize("ad_format", FORMATS)
def test_concurrent_replayed_clicks_from_different_callers_count_once(
    client, database, feed_catalog, ad_format
):
    item = issue(client, configure(database, feed_catalog, ad_format))
    token = item["receipt"]
    app = create_app()

    def click(index):
        # These transports share one running service, rather than starting and
        # stopping its lifespan (and closing shared Redis pools) in every thread.
        caller = TestClient(app, client=(f"198.51.100.{index + 1}", 51000 + index))
        try:
            for attempt in range(20):
                response = caller.post(EVENTS, json={"receipt": token, "kind": "click"})
                if response.status_code != 503:
                    return response.status_code
                sleep(0.005 * (attempt + 1))
        finally:
            caller.close()
        raise AssertionError("Attribution stayed busy after bounded HTTP retries")

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert list(executor.map(click, range(12))) == [204] * 12
    assert_counts(database, token, clicks=1, impressions=0)


def test_fresh_receipt_harvesting_cannot_bypass_issuance_budget_with_forwarded_ip_rotation(
    client, database, feed_catalog
):
    payload = configure(database, feed_catalog, "feed")
    tokens = set()
    for index in range(60):
        response = client.post(
            PUBLIC_QUERY,
            json=payload,
            headers={"X-Forwarded-For": f"203.0.113.{index + 1}"},
        )
        assert response.status_code == 200, response.text
        item = next(item for item in response.json()["items"] if item["sponsored"])
        tokens.add(item["receipt"])
        assert (
            client.post(EVENTS, json={"receipt": item["receipt"], "kind": "click"}).status_code
            == 204
        )
    assert len(tokens) == 60
    for index in range(3):
        response = client.post(
            PUBLIC_QUERY,
            json=payload,
            headers={
                "X-Forwarded-For": f"192.0.2.{index + 1}",
                "Forwarded": f"for=192.0.2.{index + 1}",
            },
        )
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
    with database() as session:
        assert session.scalar(select(func.sum(PartnerTrackedDailyMetric.clicks))) == 60
        # Issuance, rather than click submission, reserves funded capacity.
        campaign = session.get(PartnerCampaign, feed_catalog["campaign"])
        assert campaign.daily_impression_limit == 100
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPlacementDelivery)
                .where(PartnerPlacementDelivery.asset_id == uuid.UUID(item["asset_id"]))
            )
            == 60
        )
