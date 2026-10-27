"""Real, isolated ClickHouse partner metrics for transactional domain tests."""

import asyncio
import time
import uuid
from datetime import UTC, datetime

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.config import get_settings

from tests.support import analytics


class PartnerAnalyticsFixture:
    def __init__(self, settings):
        self.settings = settings

    def run(self, operation):
        async def invoke():
            async with AnalyticsClient(self.settings) as client:
                client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                return await operation(DirectEngagementAnalytics(client), client)

        return asyncio.run(invoke())

    def seed(self, asset_id, day, impressions, clicks, *, baseline=False, updated_at=None):
        self.seed_many(
            [
                dict(
                    asset_id=asset_id,
                    day=day,
                    impressions=impressions,
                    clicks=clicks,
                    updated_at=updated_at or datetime.now(UTC),
                )
            ],
            baseline=baseline,
        )

    def seed_many(self, rows, *, baseline=False):
        async def write(store, _client):
            for offset in range(0, len(rows), 1000):
                await store.partner_totals(
                    [
                        dict(
                            row,
                            revision=time.time_ns(),
                            updated_at=row.get("updated_at") or datetime.now(UTC),
                        )
                        for row in rows[offset : offset + 1000]
                    ],
                    baseline=baseline,
                )

        self.run(write)

    def publish_claim(self, receipt, kind, claim):
        async def write(store, _client):
            await store.partner_events(
                [
                    dict(
                        asset_id=receipt.asset_id,
                        delivery_id=receipt.delivery_id,
                        kind=kind,
                        day=receipt.issued_at.date(),
                        observed_at=claim.recorded_at,
                    )
                ]
            )

        self.run(write)

    def daily(self, asset_ids, *, start=None, end=None):
        async def read(store, _client):
            return await store.partner_daily(list(asset_ids), start=start, end=end)

        return self.run(read)

    def events(self):
        async def read(_store, client):
            return await client.query(
                "SELECT * FROM analytics_partner_events FINAL WHERE kind='partner_event' "
                "ORDER BY owner,identity"
            )

        return self.run(read)


@pytest.fixture
def partner_analytics(monkeypatch):
    configured = analytics.settings()
    name = "devfeed_partner_" + uuid.uuid4().hex + "_test"

    async def namespace(statement):
        async with AnalyticsClient(configured) as client:
            await client.execute(statement)

    asyncio.run(namespace(f"CREATE DATABASE `{name}`"))
    configured = configured.model_copy(update={"clickhouse_database": name})
    fixture = PartnerAnalyticsFixture(configured)

    async def initialize(store, client):
        await client.initialize()
        await client.insert(
            "analytics_backfill_progress",
            [
                dict(
                    import_id="direct-v1",
                    source=source,
                    page=1,
                    cursor="null",
                    boundary="null",
                    complete=1,
                )
                for source in (
                    "partner_daily_metrics",
                    "partner_tracked_daily_metrics",
                    "partner_delivery_events",
                )
            ],
        )

    fixture.run(initialize)
    settings = get_settings()
    for field in (
        "clickhouse_url",
        "clickhouse_database",
        "clickhouse_username",
        "clickhouse_password",
    ):
        monkeypatch.setattr(settings, field, getattr(configured, field))
    try:
        yield fixture
    finally:
        asyncio.run(namespace(f"DROP DATABASE `{name}` SYNC"))
