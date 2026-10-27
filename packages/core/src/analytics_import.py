"""One-time import of historical application analytics into ClickHouse.

Old analytical writers must be drained before this importer starts. New direct
writers use distinct live event identities. The historical importer is temporary.
"""

import math
from datetime import UTC, date, datetime

from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.analytics_backfill import AnalyticsBackfill, BackfillSource
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.partner_claim_backfill import backfill_partner_claims

IMPORT_ID = "direct-v1"


def _date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _time(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HistoricalAnalyticsImport:
    """Configure the bounded source pages and their direct ClickHouse writers."""

    def __init__(self, client, *, cutover, page_size=1000):
        if (
            not isinstance(cutover, datetime)
            or cutover.tzinfo is None
            or cutover.utcoffset() is None
        ):
            raise ValueError("A fixed, timezone-aware analytics cutover is required")
        self.client = client
        self._connection = None
        self.cutover = cutover.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        self.opens = ArticleOpenAnalytics(client)
        self.engagement = DirectEngagementAnalytics(client)
        self.inference = InferenceStore(client)
        self.importer = AnalyticsBackfill(
            client,
            IMPORT_ID,
            [
                BackfillSource(
                    "overview_daily",
                    ("day", "metrics"),
                    ("day",),
                    self.import_overview,
                    before=("day", self.cutover.date().isoformat()),
                ),
                BackfillSource(
                    "inference_calls",
                    (
                        "id",
                        "started_at",
                        "finished_at",
                        "status",
                        "operation",
                        "reason",
                        "model",
                        "reasoning_effort",
                        "tokens",
                        "web_searches",
                        "duration_ms",
                    ),
                    ("id",),
                    self.inference.write,
                ),
                BackfillSource(
                    "article_engagement",
                    ("article_id", "opens"),
                    ("article_id",),
                    self.opens.import_totals,
                ),
                BackfillSource(
                    "article_opens",
                    ("article_id", "viewer_key", "opened_hour"),
                    ("article_id", "viewer_key", "opened_hour"),
                    self.opens.import_opens,
                ),
                BackfillSource(
                    "partner_daily_metrics",
                    ("asset_id", "day", "impressions", "clicks", "updated_at"),
                    ("asset_id", "day"),
                    self.import_partner_totals,
                ),
                BackfillSource(
                    "partner_tracked_daily_metrics",
                    ("asset_id", "day", "impressions", "clicks", "updated_at"),
                    ("asset_id", "day"),
                    self.import_partner_baseline,
                ),
                BackfillSource(
                    "partner_delivery_events",
                    ("asset_id", "delivery_id", "kind", "day"),
                    ("asset_id", "delivery_id", "kind"),
                    self.import_partner_claims,
                ),
                BackfillSource(
                    "search_query_stats",
                    ("query_hash", "successful_count", "first_seen_at", "last_seen_at"),
                    ("query_hash",),
                    self.import_search_baseline,
                ),
            ],
            page_size=page_size,
        )

    async def initialize(self):
        await self.opens.initialize()
        await self.engagement.initialize()
        await self.inference.initialize()
        await self.importer.initialize()
        await self.client.execute(
            "CREATE TABLE IF NOT EXISTS analytics_audience_archive_daily (day Date, "
            "opens Nullable(UInt64),readers Nullable(Float64),accounts_created Nullable(UInt64), "
            "published Nullable(UInt64),version UInt64) "
            "ENGINE=ReplacingMergeTree(version) ORDER BY day"
        )
        await self.opens.begin_import(self.cutover)

    async def scan_one(self, connection, source):
        """Scan a page with one coordinator; claim migration uses the same connection."""
        if self._connection is not None:
            raise RuntimeError("Historical import is already processing a page")
        self._connection = connection
        try:
            return await self.importer.scan_one(connection, source)
        finally:
            self._connection = None

    async def import_partner_claims(self, rows):
        if self._connection is None:
            raise RuntimeError("A source connection is required for historical partner claims")
        await backfill_partner_claims(self._connection, rows)

    async def once(self, connection):
        """Import at most one page; a single coordinator owns each invocation."""
        order = (
            "partner_delivery_events",
            "article_engagement",
            "article_opens",
            "search_query_stats",
            "inference_calls",
            "partner_daily_metrics",
            "partner_tracked_daily_metrics",
            "overview_daily",
        )
        for name in order:
            if not await self.importer.complete([name]):
                await self.scan_one(connection, name)
                if await self.importer.complete(["article_engagement", "article_opens"]):
                    await self.mark_ready()
                return True
        await self.mark_ready()
        return False

    async def mark_ready(self):
        """Publish open totals only after both required source imports are complete."""
        await self.opens.mark_ready(self.cutover, self.importer)

    async def import_overview(self, rows):
        result = []
        for row in rows:
            metrics = row["metrics"]
            if not isinstance(metrics, dict):
                raise ValueError("Invalid historical overview")
            values = {
                key: metrics.get(key) for key in ("opens", "readers", "accounts", "published")
            }
            for key, value in values.items():
                if value is None:
                    continue
                if key == "readers":
                    if type(value) not in {int, float} or value < 0 or not math.isfinite(value):
                        raise ValueError("Invalid historical reader count")
                elif type(value) is not int or not 0 <= value < 2**64:
                    raise ValueError("Invalid historical overview count")
            result.append(
                {
                    "day": _date(row["day"]),
                    "opens": values["opens"],
                    "readers": values["readers"],
                    "accounts_created": values["accounts"],
                    "published": values["published"],
                    "version": 2**63,
                }
            )
        await self.client.insert("analytics_audience_archive_daily", result)

    async def import_partner_totals(self, rows):
        await self.engagement.partner_totals(self._partner_rows(rows), baseline=False)

    async def import_partner_baseline(self, rows):
        await self.engagement.partner_totals(self._partner_rows(rows), baseline=True)

    @staticmethod
    def _partner_rows(rows):
        return [
            {
                "asset_id": row["asset_id"],
                "day": _date(row["day"]),
                "impressions": row["impressions"],
                "clicks": row["clicks"],
                "updated_at": _time(row["updated_at"]),
                "revision": 1,
            }
            for row in rows
        ]

    async def import_search_baseline(self, rows):
        await self.engagement.search_baselines(
            [
                {
                    "query_hash": row["query_hash"],
                    "count": row["successful_count"],
                    "first_seen": _time(row["first_seen_at"]),
                    "last_seen": _time(row["last_seen_at"]),
                    "revision": 1,
                }
                for row in rows
            ]
        )
