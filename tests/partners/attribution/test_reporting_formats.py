"""Canonical products aggregate across creative ledgers before sorting and pagination."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerMembership,
    PartnerProduct,
)

from tests.partners.attribution.test_reporting_sort import dashboard
from tests.partners.promotions.test_promotions import seed_catalog
from tests.support.partner_portal import ISSUER
from tests.support.partner_portal import portal_client as portal_client

pytestmark = pytest.mark.integration


@pytest.fixture
def product_report(database, partner_analytics):
    now = datetime.now(UTC)
    today = now.date()
    with database.begin() as session:
        account = PartnerAccount(name="Native formats partner", tier="silver")
        foreign = PartnerAccount(name="Other partner", tier="gold")
        session.add_all([account, foreign])
        session.flush()
        _, product = seed_catalog(session, account.id, name="CronWatch")
        middle = PartnerProduct(
            name="Earlier CronWatch",
            product_url="https://earlier-cronwatch.example",
            tagline="Previous identity",
            merged_into_id=product,
        )
        session.add(middle)
        session.flush()
        oldest = PartnerProduct(
            name="Oldest CronWatch",
            product_url="https://oldest-cronwatch.example",
            tagline="Previous identity",
            merged_into_id=middle.id,
        )
        session.add(oldest)
        session.flush()
        session.add(PartnerMembership(account_id=account.id, issuer=ISSUER, subject="alice"))
        campaigns = {}
        for ad_format in ("engagement", "header_sponsorship"):
            campaign = PartnerCampaign(
                account_id=account.id,
                name=ad_format,
                ad_format=ad_format,
                status="active",
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=1),
            )
            session.add(campaign)
            session.flush()
            campaigns[ad_format] = campaign.id
        assets = [
            PartnerAsset(
                id=uuid.UUID(int=1),
                account_id=account.id,
                product_id=oldest.id,
                name=oldest.name,
                kind="product",
                automatic=True,
                status="paused",
            ),
            PartnerAsset(
                id=uuid.UUID(int=2),
                account_id=account.id,
                product_id=middle.id,
                name=middle.name,
                kind="ad",
                automatic=True,
                status="active",
                ad_format="engagement",
                campaign_id=campaigns["engagement"],
            ),
            PartnerAsset(
                id=uuid.UUID(int=3),
                account_id=account.id,
                product_id=product,
                name="Homepage creative",
                kind="ad",
                automatic=True,
                status="active",
                ad_format="header_sponsorship",
                campaign_id=campaigns["header_sponsorship"],
            ),
            PartnerAsset(
                id=uuid.UUID(int=4),
                account_id=account.id,
                name="Standalone sponsorship",
                kind="ad",
                status="active",
            ),
            PartnerAsset(
                id=uuid.UUID(int=5),
                account_id=foreign.id,
                product_id=product,
                name="Foreign canonical product",
                kind="ad",
                status="active",
            ),
        ]
        session.add_all(assets)
        session.flush()
        partner_analytics.seed_many(
            [
                dict(asset_id=assets[0].id, day=today, impressions=100, clicks=1),
                dict(asset_id=assets[1].id, day=today, impressions=200, clicks=10),
                dict(asset_id=assets[3].id, day=today, impressions=300, clicks=6),
                dict(asset_id=assets[4].id, day=today, impressions=99999, clicks=9999),
            ]
        )
        partner_analytics.seed_many(
            [
                dict(asset_id=assets[0].id, day=today, impressions=50, clicks=4),
                dict(
                    asset_id=assets[2].id, day=today - timedelta(days=1), impressions=50, clicks=5
                ),
            ],
            baseline=True,
        )
        return account.id, product


def test_formats_and_merged_identities_make_one_product_row_before_metric_sorting(
    portal_client, product_report
):
    client, _ = portal_client
    account, product = product_report
    result = dashboard(client, account, days=7, sort="impressions", order="desc", limit=1)
    assert result["asset_total"] == 2
    assert result["totals"] == {
        "impressions": 700,
        "clicks": 26,
        "ctr": pytest.approx(26 / 700 * 100),
        "measured_days": 2,
    }
    assert len(result["assets"]) == 1
    row = result["assets"][0]
    assert row["name"] == "CronWatch"
    assert row["aggregated"] is True
    assert row["product_id"] == str(product)
    assert row["ad_formats"] == ["engagement", "feed", "header_sponsorship"]
    assert row["impressions"] == 400
    assert row["clicks"] == 20
    assert row["ctr"] == 5
    assert row["measured_days"] == 2, "Count unique measured days across all formats"
    assert row["campaign_id"] is None, "A product row must not imply one campaign owns all metrics"
    second = dashboard(client, account, days=7, sort="impressions", order="desc", limit=1, offset=1)
    assert second["assets"][0]["name"] == "Standalone sponsorship"
    assert second["assets"][0]["aggregated"] is False
    assert second["totals"] == result["totals"]
    assert second["asset_total"] == 2


def test_format_filter_preserves_canonical_grouping_and_ledger_breakdown(
    portal_client, product_report
):
    client, _ = portal_client
    account, product = product_report
    filtered = dashboard(client, account, ad_format="engagement")
    assert filtered["asset_total"] == 1
    assert filtered["assets"][0]["product_id"] == str(product)
    assert filtered["assets"][0]["ad_formats"] == ["engagement"]
    assert filtered["totals"] == {"impressions": 200, "clicks": 10, "ctr": 5, "measured_days": 1}
    entire = dashboard(client, account)
    formats = {row["ad_format"]: row for row in entire["format_totals"]}
    assert formats["feed"]["impressions"] == 450
    assert formats["feed"]["clicks"] == 11
    assert formats["engagement"]["impressions"] == 200
    assert formats["header_sponsorship"]["impressions"] == 50


def test_mixed_manual_and_automatic_product_totals_are_explicitly_aggregated(
    database, portal_client, product_report, partner_analytics
):
    account, product = product_report
    with database.begin() as session:
        manual = PartnerAsset(
            id=uuid.UUID(int=6),
            account_id=account,
            product_id=product,
            name="Legacy imported product reporting",
            kind="ad",
            status="active",
        )
        session.add(manual)
        session.flush()
        partner_analytics.seed(
            asset_id=manual.id, day=datetime.now(UTC).date(), impressions=30, clicks=3
        )
    result = dashboard(portal_client[0], account)
    row = next(item for item in result["assets"] if item["product_id"] == str(product))
    assert row["id"] == str(uuid.UUID(int=1))
    assert row["automatic"] is False
    assert row["aggregated"] is True, "Mixed totals must never expose a representative asset editor"
    assert row["impressions"] == 430 and row["clicks"] == 23
    assert result["asset_total"] == 2
