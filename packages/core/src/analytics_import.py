"""One-time import of historical application analytics into ClickHouse.

Old analytical writers must be drained before this importer starts. New direct
writers use distinct live event identities. The historical importer is temporary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import UTC, date, datetime
from functools import partial

from psycopg import AsyncConnection

from devfeed_core.analysis import _PreparedCatalog, reuse_candidates
from devfeed_core.analytics import AnalyticsClient, ReconciliationBusy, SqlRecord
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.analytics_backfill import AnalyticsBackfill, BackfillSource
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.analytics_schema import JOB_TABLES, TABLES
from devfeed_core.catalog_cache import SnapshotCache, reuse_snapshots
from devfeed_core.config import Settings
from devfeed_core.partner_claim_backfill import backfill_partner_claims

IMPORT_ID = "direct-v1"
logger = logging.getLogger(__name__)
OVERVIEW_SOURCES = (
    "articles",
    "sources",
    "topics",
    "user_accounts",
    "user_topics",
    "user_interests",
    "user_recommendation_states",
    "topic_proposals",
    *JOB_TABLES,
)


def _date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _time(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _finished_card_builder(task: asyncio.Task[list[SqlRecord]]) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.warning(
            "analytics_import_card_builder_failed", extra={"error_type": type(error).__name__}
        )


class HistoricalAnalyticsImport:
    """Configure the bounded source pages and their direct ClickHouse writers."""

    def __init__(
        self,
        client: AnalyticsClient,
        *,
        cutover: datetime,
        page_size: int = 1000,
        settings: Settings | None = None,
    ) -> None:
        if (
            not isinstance(cutover, datetime)
            or cutover.tzinfo is None
            or cutover.utcoffset() is None
        ):
            raise ValueError("A fixed, timezone-aware analytics cutover is required")
        from devfeed_core.config import get_settings

        self.client = client
        self.settings = settings or get_settings()
        self._connection: AsyncConnection[SqlRecord] | None = None
        self._card_catalogs: SnapshotCache = {}
        self._card_ranking: list[_PreparedCatalog] = []
        self._card_builder: asyncio.Task[list[SqlRecord]] | None = None
        self._card_active = False
        self.cutover = cutover.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        self.opens = ArticleOpenAnalytics(client)
        self.engagement = DirectEngagementAnalytics(client)
        self.inference = InferenceStore(client)
        self.overview = DirectOverviewAnalytics(client)
        self.absent: list[str] = []
        from devfeed_core.analytics_direct_cards import CARD_SOURCES
        from devfeed_core.analytics_observation import pipeline_policy_version

        policy = pipeline_policy_version(self.settings)
        self.cards_importer = AnalyticsBackfill(
            client,
            "cards-" + hashlib.sha256(policy.encode()).hexdigest()[:32],
            [
                BackfillSource(name, ("id",), ("id",), partial(self.import_cards, kind))
                for name, kind in CARD_SOURCES.items()
            ],
            page_size=min(page_size, 200),
        )
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
        for name in ("topic_decision_calls", "inference_daily_usage"):
            if name == "topic_decision_calls":
                spec = TABLES[name]
                fields, keys = spec.fields, spec.keys
            else:
                fields = (
                    "day",
                    "operation",
                    "model",
                    "model_is_null",
                    "effort",
                    "calls",
                    "returned",
                    "failed",
                    "running",
                    "unreported",
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "web_searches",
                    "duration_ms",
                )
                keys = ("day", "operation", "model", "model_is_null", "effort")
            self.importer.sources[name] = BackfillSource(
                name, fields, keys, partial(self.archive_history, name, keys)
            )
        for name in OVERVIEW_SOURCES:
            spec = TABLES[name]
            self.importer.sources[name] = BackfillSource(
                name, spec.fields, spec.keys, partial(self.import_source, name)
            )

    async def configure(self, connection: AsyncConnection[SqlRecord]) -> None:
        """Inspect retained PostgreSQL history without depending on draft revision numbers."""
        from devfeed_core.analytics_cutover import LEGACY_TABLES

        rows = await (
            await connection.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=ANY(%s)",
                (list(self.importer.sources),),
            )
        ).fetchall()
        columns: dict[str, set[str]] = {}
        for row in rows:
            name, field = tuple(row.values()) if isinstance(row, dict) else row
            columns.setdefault(name, set()).add(field)
        for name, source in self.importer.sources.items():
            if not set(source.fields) <= columns.get(name, set()):
                if name not in LEGACY_TABLES and name != "search_query_stats":
                    raise RuntimeError(f"Historical source schema is incomplete: {name}")
                self.absent.append(name)

    async def initialize(self) -> None:
        """Prepare data checkpoints after centralized schema migration."""
        for name in self.absent:
            cursor, page, complete = await self.importer._progress(name)
            if complete:
                continue
            if page or cursor:
                raise RuntimeError(f"Historical source disappeared before completion: {name}")
            source = self.importer.sources[name]
            await self.client.insert(
                "analytics_backfill_progress",
                [
                    {
                        "import_id": IMPORT_ID,
                        "source": name,
                        "page": 1,
                        "cursor": "",
                        "complete": 1,
                        "boundary": source.before[1] if source.before else "",
                    }
                ],
            )
        await self.opens.begin_import(self.cutover)

    async def scan_one(self, connection: AsyncConnection[SqlRecord], source: str) -> bool:
        """Scan a page with one coordinator; claim migration uses the same connection."""
        if self._connection is not None:
            raise RuntimeError("Historical import is already processing a page")
        self._connection = connection
        try:
            return await self.importer.scan_one(connection, source)
        finally:
            self._connection = None

    async def import_partner_claims(self, rows: list[SqlRecord]) -> None:
        if self._connection is None:
            raise RuntimeError("A source connection is required for historical partner claims")
        await backfill_partner_claims(self._connection, rows)
        await self.archive_history(
            "partner_delivery_events", ("asset_id", "delivery_id", "kind"), rows
        )

    async def once(self, connection: AsyncConnection[SqlRecord]) -> bool:
        """Import at most one page; a single coordinator owns each invocation."""
        if self._card_active or (self._card_builder is not None and not self._card_builder.done()):
            raise ReconciliationBusy("builder_busy")
        order = (
            "partner_delivery_events",
            "article_engagement",
            "article_opens",
            "search_query_stats",
            "inference_calls",
            "partner_daily_metrics",
            "partner_tracked_daily_metrics",
            "overview_daily",
            "topic_decision_calls",
            "inference_daily_usage",
            *OVERVIEW_SOURCES,
        )
        for name in order:
            if not await self.importer.complete([name]):
                await self.scan_one(connection, name)
                if await self.importer.complete(["article_engagement", "article_opens"]):
                    await self.mark_ready()
                return True
        await self.mark_ready()
        for name in self.cards_importer.sources:
            if not await self.cards_importer.complete([name]):
                await self.cards_importer.scan_one(connection, name)
                return True
        self._card_catalogs.clear()
        self._card_ranking.clear()
        self._card_builder = None
        return False

    async def import_cards(self, kind: str, rows: list[SqlRecord]) -> None:
        from devfeed_core.analytics_card_builders import bounded_ids
        from devfeed_core.analytics_direct_cards import build_cards, identity

        if self._card_active or (self._card_builder is not None and not self._card_builder.done()):
            raise ReconciliationBusy("builder_busy")
        identifiers = bounded_ids(sorted({identity(row["id"]) for row in rows}))
        if not identifiers:
            return

        def build() -> list[SqlRecord]:
            # Each page still checks transactional revisions. Retain only immutable
            # catalog/ranking inputs, never publication decisions or source sessions.
            with (
                reuse_snapshots(
                    retained=self._card_catalogs, on_invalidate=self._card_ranking.clear
                )
                if self.settings.cache_enabled
                else nullcontext(),
                reuse_candidates(
                    retained=self._card_ranking if self.settings.cache_enabled else None
                ),
            ):
                return build_cards(self.settings, kind, identifiers, 1)

        self._card_active = True
        task = self._card_builder = asyncio.create_task(asyncio.to_thread(build))
        try:
            observations = await asyncio.shield(task)
            await self.client.insert("analytics_card_canonical", observations)
        except asyncio.CancelledError:
            # A native thread cannot be cancelled. Refuse reentry until it exits;
            # a cancelled build's result is never inserted. An interrupted insert
            # can have reached the sink, but its source checkpoint still cannot
            # advance and the unchanged baseline identity makes replay safe.
            task.add_done_callback(_finished_card_builder)
            raise
        finally:
            self._card_active = False
            if task.done():
                self._card_builder = None

    async def import_source(self, name: str, rows: list[SqlRecord]) -> None:
        from devfeed_core.analytics_producer import hydrate_changes

        if self._connection is None:
            raise RuntimeError("A source connection is required for historical snapshots")
        changes = [{"source_table": name, "record": row, "operation": "I"} for row in rows]
        changes = await hydrate_changes(self._connection, changes)
        for offset in range(0, len(changes), 1000):
            await self.overview.publish(changes[offset : offset + 1000], revision=1)

    async def archive_history(
        self, source: str, keys: Sequence[str], rows: list[SqlRecord]
    ) -> None:
        await self.client.insert(
            "analytics_history_archive",
            [
                {
                    "source_table": source,
                    "identity": json.dumps(
                        [row[key] for key in keys], default=str, separators=(",", ":")
                    ),
                    "record": json.dumps(row, default=str, separators=(",", ":")),
                }
                for row in rows
            ],
        )

    async def mark_ready(self) -> None:
        """Publish open totals only after both required source imports are complete."""
        await self.opens.mark_ready(self.cutover, self.importer)

    async def import_overview(self, rows: list[SqlRecord]) -> None:
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

    async def import_partner_totals(self, rows: list[SqlRecord]) -> None:
        await self.engagement.partner_totals(self._partner_rows(rows), baseline=False)

    async def import_partner_baseline(self, rows: list[SqlRecord]) -> None:
        await self.engagement.partner_totals(self._partner_rows(rows), baseline=True)

    @staticmethod
    def _partner_rows(rows: list[SqlRecord]) -> list[SqlRecord]:
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

    async def import_search_baseline(self, rows: list[SqlRecord]) -> None:
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
