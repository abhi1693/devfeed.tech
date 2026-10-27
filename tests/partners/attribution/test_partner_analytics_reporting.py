"""Direct ClickHouse metrics preserve partner reporting scope and ordering."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from devfeed_http import partner_analytics_reporting as report


def asset(
    account, account_name, identity, name, *, product=None, ad_format="feed", status="active"
):
    return {
        "id": identity,
        "account_id": account,
        "account_name": account_name,
        "canonical_product_id": product,
        "product_name": "Grouped product" if product else None,
        "name": name,
        "kind": "ad",
        "status": status,
        "automatic": True,
        "ad_format": ad_format,
        "campaign_id": None,
        "article_id": None,
    }


class Store:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def partner_daily(self, ids, *, start, end):
        self.calls.append(list(ids))
        return [row for row in self.values if row["owner"] in ids and start <= row["day"] <= end]


def test_direct_report_groups_all_scoped_assets_before_sorting_and_pagination(monkeypatch):
    async def run():
        today = datetime.now(UTC).date()
        account, product = uuid.uuid4(), uuid.uuid4()
        first, second, other, empty = (uuid.uuid4() for _ in range(4))
        assets = [
            asset(account, "Company", first, "First", product=product),
            asset(account, "Company", second, "Second", product=product, ad_format="header"),
            asset(account, "Company", other, "Other"),
            asset(account, "Company", empty, "Unmeasured"),
        ]
        stamp = datetime.now(UTC).isoformat()
        values = [
            {
                "owner": str(first),
                "day": today,
                "impressions": 10,
                "clicks": 1,
                "updated_at": stamp,
            },
            {
                "owner": str(second),
                "day": today,
                "impressions": 30,
                "clicks": 2,
                "updated_at": stamp,
            },
            {
                "owner": str(other),
                "day": today,
                "impressions": 100,
                "clicks": 5,
                "updated_at": stamp,
            },
        ]
        store = Store(values)
        result = await report.reporting_metrics(
            assets, store, limit=1, sort="impressions", order="desc"
        )
        assert result["asset_total"] == 3
        assert result["assets"][0]["name"] == "Other"
        assert result["totals"] == {
            "impressions": 140,
            "clicks": 8,
            "ctr": 8 / 140 * 100,
            "measured_days": 1,
        }
        assert result["trend"] == [{"day": today, "impressions": 140, "clicks": 8}]
        assert {row["ad_format"] for row in result["format_totals"]} == {"feed", "header"}
        second_page = await report.reporting_metrics(
            assets, store, limit=1, offset=1, sort="impressions", order="desc"
        )
        assert second_page["assets"][0]["name"] == "Grouped product"
        assert second_page["assets"][0]["aggregated"]
        assert second_page["assets"][0]["measured_days"] == 1
        assert second_page["assets"][0]["impressions"] == 40
        final_page = await report.reporting_metrics(
            assets, store, limit=1, offset=2, sort="impressions", order="desc"
        )
        assert final_page["assets"][0]["name"] == "Unmeasured"
        assert final_page["assets"][0]["ctr"] is None

    asyncio.run(run())


def test_large_authorized_scope_is_queried_in_bounded_batches(monkeypatch):
    async def run():
        account = uuid.uuid4()
        assets = [asset(account, "Company", uuid.uuid4(), str(i)) for i in range(101)]
        store = Store([])
        result = await report.reporting_metrics(assets, store)
        assert result["asset_total"] == 101
        assert [len(batch) for batch in store.calls] == [50, 50, 1]

    asyncio.run(run())


def test_metrics_outside_authorized_scope_are_rejected(monkeypatch):
    async def run():
        account, identity = uuid.uuid4(), uuid.uuid4()
        assets = [asset(account, "Company", identity, "Name")]

        class Unsafe:
            async def partner_daily(self, *_args, **_kwargs):
                return [
                    {
                        "owner": str(uuid.uuid4()),
                        "day": datetime.now(UTC).date(),
                        "impressions": 1,
                        "clicks": 0,
                        "updated_at": datetime.now(UTC),
                    }
                ]

        with pytest.raises(ValueError, match="Unauthorized"):
            await report.reporting_metrics(assets, Unsafe())

    asyncio.run(run())


@pytest.mark.integration
def test_direct_report_preserves_metrics_and_account_scope_for_all_sorts(
    database, partner_analytics
):
    from devfeed_core.models import PartnerAccount, PartnerAsset
    from sqlalchemy import select

    async def run(metadata, values):
        for sort in ("name", "account", "impressions", "clicks", "ctr"):
            for order in ("asc", "desc"):
                actual = await report.reporting_metrics(
                    metadata,
                    Store(values),
                    days=30,
                    limit=2,
                    offset=1,
                    sort=sort,
                    order=order,
                )
                assert actual["asset_total"] == 3
                assert actual["totals"] == {
                    "impressions": 7,
                    "clicks": 4,
                    "ctr": 4 / 7 * 100,
                    "measured_days": 1,
                }
                names = [row["name"] for row in actual["assets"]]
                expected_first = (
                    "Alpha" if sort in {"impressions", "clicks"} and order == "asc" else "beta"
                )
                assert names[0] == expected_first
                assert "Private" not in names

    now = datetime.now(UTC)
    with database.begin() as session:
        account = PartnerAccount(name="Visible", tier="bronze")
        foreign = PartnerAccount(name="Hidden", tier="bronze")
        session.add_all([account, foreign])
        session.flush()
        assets = []
        for owner, name in (
            (account, "Alpha"),
            (account, "beta"),
            (account, "Unmeasured"),
            (foreign, "Private"),
        ):
            item = PartnerAsset(account_id=owner.id, name=name, kind="ad", status="active")
            session.add(item)
            assets.append(item)
        session.flush()
        for item, first, second in ((assets[0], 5, 3), (assets[1], 0, 0), (assets[3], 900, 1)):
            partner_analytics.seed(
                asset_id=item.id, day=now.date(), impressions=first, clicks=second, updated_at=now
            )
        partner_analytics.seed(
            asset_id=assets[0].id,
            day=now.date(),
            impressions=2,
            clicks=1,
            updated_at=now,
            baseline=True,
        )
        session.flush()
        values = partner_analytics.daily([row.id for row in assets])
        metadata = report.reporting_assets(
            session, select(PartnerAccount).where(PartnerAccount.id == account.id)
        )
        asyncio.run(run(metadata, values))


@pytest.mark.integration
def test_direct_report_matches_canonical_products_and_format_totals(database, partner_analytics):
    from devfeed_core.models import PartnerAccount, PartnerAsset, PartnerProduct
    from sqlalchemy import select

    now = datetime.now(UTC)
    with database.begin() as session:
        account = PartnerAccount(name="Partner", tier="gold")
        current = PartnerProduct(
            name="Current", product_url="https://current.example", tagline="Current"
        )
        session.add_all([account, current])
        session.flush()
        old = PartnerProduct(
            name="Old", product_url="https://old.example", tagline="Old", merged_into_id=current.id
        )
        session.add(old)
        session.flush()
        first = PartnerAsset(
            account_id=account.id,
            product_id=old.id,
            name="Old ad",
            kind="ad",
            status="paused",
            ad_format="engagement",
            automatic=False,
        )
        second = PartnerAsset(
            account_id=account.id,
            product_id=current.id,
            name="New ad",
            kind="ad",
            status="active",
            ad_format="feed",
            automatic=False,
        )
        session.add_all([first, second])
        session.flush()
        values = []
        for row, impressions, clicks in ((first, 10, 1), (second, 20, 2)):
            partner_analytics.seed(
                asset_id=row.id,
                day=now.date(),
                impressions=impressions,
                clicks=clicks,
                updated_at=now,
            )
            values.append(
                {
                    "owner": str(row.id),
                    "day": now.date(),
                    "impressions": impressions,
                    "clicks": clicks,
                    "updated_at": now.isoformat(),
                }
            )
        session.flush()
        scope = select(PartnerAccount).where(PartnerAccount.id == account.id)
        values = partner_analytics.daily([first.id, second.id])
        metadata = report.reporting_assets(session, scope)
        actual = asyncio.run(report.reporting_metrics(metadata, Store(values)))
        assert actual["asset_total"] == 1
        assert actual["totals"] == {"impressions": 30, "clicks": 3, "ctr": 10.0, "measured_days": 1}
        assert actual["assets"][0]["name"] == "Current"
        assert actual["assets"][0]["ad_formats"] == ["engagement", "feed"]


def test_reporting_requires_completed_history_and_claim_import():
    from devfeed_core.analytics import AnalyticsUnavailable

    class Checkpoints:
        def __init__(self, names):
            self.names = names

        async def query(self, _statement, _params):
            return [{"source": name, "complete": 1} for name in self.names]

    async def run():
        complete = {
            "partner_daily_metrics",
            "partner_tracked_daily_metrics",
            "partner_delivery_events",
        }
        await report.require_partner_history(Checkpoints(complete))
        await report.require_partner_claims(Checkpoints({"partner_delivery_events"}))
        with pytest.raises(AnalyticsUnavailable):
            await report.require_partner_history(
                Checkpoints(complete - {"partner_delivery_events"})
            )
        with pytest.raises(AnalyticsUnavailable):
            await report.require_partner_claims(Checkpoints(set()))

    asyncio.run(run())


@pytest.mark.parametrize(
    "sort,order,expected",
    [
        ("name", "asc", ["Alpha", "beta", "unknown", "Zed"]),
        ("name", "desc", ["Zed", "unknown", "beta", "Alpha"]),
        ("account", "asc", ["beta", "unknown", "Zed", "Alpha"]),
        ("account", "desc", ["Alpha", "beta", "unknown", "Zed"]),
        ("impressions", "asc", ["Zed", "Alpha", "beta", "unknown"]),
        ("impressions", "desc", ["beta", "Alpha", "Zed", "unknown"]),
        ("clicks", "asc", ["Zed", "Alpha", "beta", "unknown"]),
        ("clicks", "desc", ["Alpha", "beta", "Zed", "unknown"]),
        ("ctr", "asc", ["beta", "Alpha", "unknown", "Zed"]),
        ("ctr", "desc", ["Alpha", "beta", "unknown", "Zed"]),
    ],
)
def test_native_metric_ordering_keeps_unmeasured_values_and_stable_ties_explicit(
    sort, order, expected
):
    async def run():
        today = datetime.now(UTC).date()
        account = uuid.uuid4()
        rows = [
            asset(account, company, uuid.UUID(int=i + 1), name)
            for i, (name, company) in enumerate(
                [
                    ("Alpha", "Two"),
                    ("beta", "One"),
                    ("Zed", "One"),
                    ("unknown", "One"),
                ]
            )
        ]
        values = [
            {
                "owner": str(row["id"]),
                "day": today,
                "impressions": impressions,
                "clicks": clicks,
                "updated_at": datetime(2026, 1, 1) if i == 0 else datetime(2026, 1, 2, tzinfo=UTC),
            }
            for i, (row, impressions, clicks) in enumerate(
                zip(rows[:3], [10, 20, 0], [1, 1, 0], strict=True)
            )
        ]
        result = await report.reporting_metrics(rows, Store(values), sort=sort, order=order)
        assert [row["name"] for row in result["assets"]] == expected
        assert result["last_updated_at"] == datetime(2026, 1, 2, tzinfo=UTC)
        assert result["totals"]["impressions"] == 30 and result["totals"]["clicks"] == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "options",
    [
        {"sort": "invalid"},
        {"order": "invalid"},
        {"days": 0},
        {"days": 366},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
    ],
)
def test_invalid_native_report_ranges_fail_before_any_data_read(options):
    from unittest.mock import AsyncMock

    async def run():
        store = type("Store", (), {"partner_daily": AsyncMock()})()
        with pytest.raises(ValueError):
            await report.reporting_metrics([], store, **options)
        store.partner_daily.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("sort", ["name", "account", "clicks"])
def test_identical_native_names_and_metrics_use_stable_asset_identity(sort):
    async def run():
        account = uuid.uuid4()
        rows = [asset(account, "Same", uuid.UUID(int=i), "Same") for i in (2, 1)]
        result = await report.reporting_metrics(rows, Store([]), sort=sort)
        assert [row["id"] for row in result["assets"]] == [uuid.UUID(int=1), uuid.UUID(int=2)]

    asyncio.run(run())
