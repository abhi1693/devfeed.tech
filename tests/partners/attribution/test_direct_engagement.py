"""Direct partner/search observations remain idempotent and privacy-preserving."""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from devfeed_core.analytics_direct_engagement import MAX_BATCH, DirectEngagementAnalytics


class Client:
    def __init__(self):
        self.rows = []
        self.queries = []

    async def insert(self, table, rows):
        assert table in {"analytics_partner_events", "analytics_search_events"}
        assert all(
            row["kind"].startswith("partner_") == (table == "analytics_partner_events")
            for row in rows
        )
        self.rows.extend(rows)

    async def query(self, statement, parameters):
        self.queries.append((statement, parameters))
        return []


def test_events_are_sanitized_strictly_and_accept_only_stable_identifiers():
    async def run():
        client = Client()
        analytics = DirectEngagementAnalytics(client)
        asset, delivery, query = uuid.uuid4(), uuid.uuid4(), "a" * 64
        await analytics.partner_events(
            [
                {
                    "asset_id": asset,
                    "delivery_id": delivery,
                    "kind": "impression",
                    "day": date(2026, 1, 1),
                    "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
                {
                    "asset_id": asset,
                    "delivery_id": delivery,
                    "kind": "click",
                    "day": date(2026, 1, 1),
                    "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            ]
        )
        await analytics.search_events(
            [
                {
                    "query_hash": query,
                    "event_id": uuid.uuid4(),
                    "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ]
        )
        assert client.rows[0]["identity"] != client.rows[1]["identity"]
        assert client.rows[0]["impressions"] == 1 and client.rows[1]["clicks"] == 1
        assert client.rows[2]["owner"] == query and client.rows[2]["searches"] == 1
        with pytest.raises(ValueError):
            await analytics.search_events(
                [
                    {
                        "query_hash": query,
                        "event_id": uuid.uuid4(),
                        "occurred_at": datetime.now(UTC),
                        "query": "private",
                    }
                ]
            )
        assert len(client.rows) == 3

    asyncio.run(run())


def test_invalid_batches_and_lookup_boundaries_are_rejected_before_io():
    async def run():
        client = Client()
        analytics = DirectEngagementAnalytics(client)
        with pytest.raises(ValueError):
            await analytics.partner_events([{}] * (MAX_BATCH + 1))
        with pytest.raises(ValueError):
            await analytics.partner_totals(
                [
                    {
                        "asset_id": uuid.uuid4(),
                        "day": date.today(),
                        "impressions": -1,
                        "clicks": 0,
                        "revision": 1,
                        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
                    }
                ]
            )
        with pytest.raises(ValueError):
            await analytics.search_counts(["short"])
        with pytest.raises(ValueError):
            await analytics.partner_daily(
                [uuid.uuid4()], start=date(2026, 2, 1), end=date(2026, 1, 1)
            )
        assert not client.rows and not client.queries

    asyncio.run(run())


def test_authorized_lookup_uses_final_and_filters_dates_after_latest_versions():
    async def run():
        client = Client()
        analytics = DirectEngagementAnalytics(client)
        asset = uuid.uuid4()
        await analytics.partner_daily([asset, asset], start=date(2026, 1, 1))
        statement, params = client.queries[0]
        assert "FINAL" in statement and "enable_optimize_predicate_expression=0" in statement
        assert params["owners"] == [str(asset)]
        await analytics.search_counts(["b" * 64, "b" * 64])
        assert client.queries[1][1]["owners"] == ["b" * 64]

    asyncio.run(run())


@pytest.mark.integration
def test_real_clickhouse_deduplicates_receipts_and_revisions_without_private_query_data():
    import os

    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.config import Settings

    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for ClickHouse integration")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    settings = Settings(
        _env_file=None,
        database_url=os.environ["DEVFEED_TEST_DATABASE_URL"],
        redis_url=os.environ["DEVFEED_TEST_REDIS_URL"],
        clickhouse_url=url,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )

    async def run():
        target = f"direct_engagement_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    analytics = DirectEngagementAnalytics(client)
                    await analytics.initialize()
                    asset, delivery, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                    day, query = date(2026, 1, 1), "c" * 64
                    total = {
                        "asset_id": asset,
                        "day": day,
                        "impressions": 4,
                        "clicks": 1,
                        "revision": 1,
                        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
                    }
                    baseline = {**total, "impressions": 12, "clicks": 3}
                    await analytics.partner_totals([baseline], baseline=True)
                    await analytics.partner_totals([total])
                    await analytics.partner_totals(
                        [{**total, "impressions": 5, "clicks": 2, "revision": 2}]
                    )
                    await analytics.partner_totals([total])
                    impression = {
                        "asset_id": asset,
                        "delivery_id": delivery,
                        "kind": "impression",
                        "day": day,
                        "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
                    }
                    click = {**impression, "kind": "click"}
                    await analytics.partner_events([impression, click, impression])
                    summary = await analytics.partner_summary([asset])
                    assert summary == [{"owner": str(asset), "impressions": 18, "clicks": 6}]
                    assert await analytics.partner_daily([asset], start=day, end=day) == [
                        {
                            "owner": str(asset),
                            "day": day,
                            "impressions": 18,
                            "clicks": 6,
                            "updated_at": "2026-01-01T00:00:00.000000Z",
                        }
                    ]
                    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
                    await analytics.search_baselines(
                        [
                            {
                                "query_hash": query,
                                "count": 10,
                                "first_seen": timestamp,
                                "last_seen": timestamp,
                                "revision": 1,
                            }
                        ]
                    )
                    observed = {"query_hash": query, "event_id": event_id, "occurred_at": timestamp}
                    await analytics.search_events([observed, observed])
                    result = await analytics.search_counts([query])
                    assert len(result) == 1 and int(result[0]["successful_count"]) == 11
                    assert result[0]["query_hash"] == query
                    latest = {**total, "asset_id": uuid.uuid4(), "day": date.max}
                    await analytics.partner_totals([latest])
                    assert (await analytics.partner_daily([latest["asset_id"]], start=date.max))[0][
                        "day"
                    ] == date.max
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
