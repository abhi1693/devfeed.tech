"""Partner report ordering covers the entire scoped, combined-metric collection."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDailyMetric,
    PartnerMembership,
    PartnerTrackedDailyMetric,
)
from test_partner_portal import ISSUER
from test_partner_portal import portal_client as portal_client

pytestmark = pytest.mark.integration

ORDERS = {
    ("name", "asc"): ["alpha", "Beta", "Combined", "unknown", "ZERO"],
    ("name", "desc"): ["ZERO", "unknown", "Combined", "Beta", "alpha"],
    ("impressions", "asc"): ["ZERO", "Beta", "alpha", "Combined", "unknown"],
    ("impressions", "desc"): ["Combined", "alpha", "Beta", "ZERO", "unknown"],
    ("clicks", "asc"): ["ZERO", "alpha", "Beta", "Combined", "unknown"],
    ("clicks", "desc"): ["Combined", "Beta", "alpha", "ZERO", "unknown"],
    ("ctr", "asc"): ["alpha", "Combined", "Beta", "unknown", "ZERO"],
    ("ctr", "desc"): ["Beta", "Combined", "alpha", "unknown", "ZERO"],
}


@pytest.fixture
def report(database):
    today = datetime.now(UTC).date()
    with database.begin() as session:
        account = PartnerAccount(name="Visible partner", tier="bronze")
        other = PartnerAccount(name="Private partner", tier="diamond")
        session.add_all([account, other])
        session.flush()
        session.add(PartnerMembership(account_id=account.id, issuer=ISSUER, subject="alice"))
        assets = {}
        for number, name in enumerate(("alpha", "Beta", "Combined", "ZERO", "unknown"), 100):
            asset = PartnerAsset(
                id=uuid.UUID(int=number),
                account_id=account.id,
                name=name,
                kind="ad",
                status="active",
            )
            session.add(asset)
            assets[name] = asset.id
        foreign = PartnerAsset(
            account_id=other.id, name="A foreign leader", kind="ad", status="active"
        )
        session.add(foreign)
        session.flush()
        session.add_all(
            [
                PartnerDailyMetric(asset_id=assets["alpha"], day=today, impressions=100, clicks=5),
                PartnerTrackedDailyMetric(
                    asset_id=assets["Beta"], day=today, impressions=40, clicks=8
                ),
                PartnerDailyMetric(
                    asset_id=assets["Combined"], day=today, impressions=50, clicks=4
                ),
                PartnerTrackedDailyMetric(
                    asset_id=assets["Combined"], day=today, impressions=60, clicks=8
                ),
                PartnerTrackedDailyMetric(
                    asset_id=assets["ZERO"], day=today, impressions=0, clicks=0
                ),
                PartnerDailyMetric(
                    asset_id=assets["unknown"],
                    day=today - timedelta(days=40),
                    impressions=4000,
                    clicks=4,
                ),
                PartnerDailyMetric(asset_id=foreign.id, day=today, impressions=99999, clicks=9999),
            ]
        )
        return account.id, other.id, assets, today


def dashboard(client, account_id, **params):
    response = client.get(f"/v1/partner/accounts/{account_id}/dashboard", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("sort,order", ORDERS)
def test_every_column_sorts_combined_metrics_globally_before_pagination(
    portal_client, report, sort, order
):
    client, _ = portal_client
    account_id, _, _, _ = report
    params = {"sort": sort, "order": order, "days": 7}
    entire = dashboard(client, account_id, **params)
    assert [asset["name"] for asset in entire["assets"]] == ORDERS[(sort, order)]
    assert entire["asset_total"] == 5
    assert entire["totals"] == {"impressions": 250, "clicks": 25, "ctr": 10.0, "measured_days": 1}
    pages = [
        dashboard(client, account_id, limit=2, offset=offset, **params) for offset in (0, 2, 4)
    ]
    assert [asset["id"] for page in pages for asset in page["assets"]] == [
        asset["id"] for asset in entire["assets"]
    ]
    assert all(page["totals"] == entire["totals"] and page["asset_total"] == 5 for page in pages)
    values = {asset["name"]: asset for asset in entire["assets"]}
    assert values["Combined"]["impressions"] == 110 and values["Combined"]["clicks"] == 12
    assert values["Combined"]["ctr"] == pytest.approx(12 / 110 * 100)
    assert values["ZERO"]["impressions"] == values["unknown"]["impressions"] == 0
    assert values["ZERO"]["measured_days"] == 1 and values["unknown"]["measured_days"] == 0
    assert values["ZERO"]["ctr"] is values["unknown"]["ctr"] is None


@pytest.mark.parametrize("sort,order", ORDERS)
def test_equal_primary_values_use_case_insensitive_name_then_stable_identity(
    portal_client, database, report, sort, order
):
    client, _ = portal_client
    account_id, _, _, today = report
    lower_id, higher_id = uuid.UUID(int=1), uuid.UUID(int=2)
    with database.begin() as session:
        session.add_all(
            [
                PartnerAsset(
                    id=higher_id, account_id=account_id, name="TIE", kind="ad", status="active"
                ),
                PartnerAsset(
                    id=lower_id, account_id=account_id, name="tie", kind="ad", status="active"
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                PartnerDailyMetric(asset_id=identifier, day=today, impressions=12, clicks=1)
                for identifier in (higher_id, lower_id)
            ]
        )
    result = dashboard(client, account_id, sort=sort, order=order)
    tied = [asset["id"] for asset in result["assets"] if asset["name"].lower() == "tie"]
    assert tied == [str(lower_id), str(higher_id)]


def test_sorting_preserves_period_account_scope_and_default_name_order(portal_client, report):
    client, _ = portal_client
    account_id, foreign_account, _, today = report
    default = dashboard(client, account_id)
    assert [asset["name"] for asset in default["assets"]] == ORDERS[("name", "asc")]
    recent = dashboard(client, account_id, days=7, sort="impressions", order="desc")
    assert recent["start"] == (today - timedelta(days=6)).isoformat()
    assert recent["end"] == today.isoformat()
    assert recent["trend"] == [{"day": today.isoformat(), "impressions": 250, "clicks": 25}]
    assert recent["totals"] == default["totals"]
    older = dashboard(client, account_id, days=90, sort="impressions", order="desc")
    assert older["assets"][0]["name"] == "unknown"
    assert older["totals"]["impressions"] == 4250 and older["totals"]["clicks"] == 29
    assert older["totals"]["measured_days"] == 2
    assert (
        client.get(
            f"/v1/partner/accounts/{foreign_account}/dashboard?sort=clicks&order=desc"
        ).status_code
        == 404
    )


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_defined_zero_ctr_precedes_assets_without_a_denominator(
    portal_client, database, report, order
):
    client, _ = portal_client
    account_id, _, _, today = report
    with database.begin() as session:
        asset = PartnerAsset(
            account_id=account_id, name="No clicks yet", kind="ad", status="active"
        )
        session.add(asset)
        session.flush()
        session.add(PartnerDailyMetric(asset_id=asset.id, day=today, impressions=20, clicks=0))
    result = dashboard(client, account_id, sort="ctr", order=order)
    names = [asset["name"] for asset in result["assets"]]
    assert names[-2:] == ["unknown", "ZERO"]
    assert names[0 if order == "asc" else -3] == "No clicks yet"
    defined_zero = next(asset for asset in result["assets"] if asset["name"] == "No clicks yet")
    assert defined_zero["ctr"] == 0.0


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "unsupported"},
        {"sort": "Name"},
        {"order": "down"},
        {"order": "DESC"},
    ],
)
def test_unknown_sort_fields_and_directions_fail_validation(portal_client, report, params):
    client, _ = portal_client
    account_id, _, _, _ = report
    assert (
        client.get(f"/v1/partner/accounts/{account_id}/dashboard", params=params).status_code == 422
    )
