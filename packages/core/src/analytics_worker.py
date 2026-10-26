"""Durable asynchronous analytical export; no historical polling after backfill.

Run ``python -m devfeed_core.analytics_worker``. One lease owns bounded keyset
backfill and exact-ID outbox acknowledgement. ClickHouse failures leave pending
facts intact; replay and delayed commits cannot skip or double-count facts.
"""

import argparse
import asyncio
import contextlib
import logging
import math
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import sql
from psycopg.errors import LockNotAvailable
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_cards import CardPublisher
from devfeed_core.analytics_gauges import Gauges
from devfeed_core.analytics_rollups import RollupPublisher
from devfeed_core.analytics_schema import BACKFILL_TABLES, TABLES
from devfeed_core.analytics_transform import transform_events
from devfeed_core.analytics_upgrade import V1_KEYS, NativeUpgrade
from devfeed_core.config import get_settings
from devfeed_core.logging import configure_logging
from devfeed_core.models import Base
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.telemetry import background_cycle, current, start_runtime, stop_runtime

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120
GAUGE_BATCH_SIZE = 16
GAUGE_CYCLE_SECONDS = 0.25
RAW_COVERAGE = (
    "NOT EXISTS(SELECT 1 FROM analytics_bootstrap_cursors WHERE NOT complete) AS sources_complete,"
    "CASE WHEN native_bootstrap_complete THEN true ELSE "
    "NOT EXISTS(SELECT 1 FROM analytics_ingest_events "
    "WHERE format_version=1) END AS legacy_complete"
)


def postgres_dsn(settings):
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


class AnalyticsExporter:
    def __init__(self, client, settings=None):
        self.settings = settings or get_settings()
        self.client = client
        self.token = uuid.uuid4()
        self._last_status = 0.0
        self._last_ready = None
        self.gauges = Gauges(self.settings, self.client)
        self.rollups = RollupPublisher(self.client)
        # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Retire the cutover importer with
        # its callers after ANALYTICS_BACKFILL_RETIREMENT.md's release gates pass.
        self.upgrade = NativeUpgrade(self.settings, self.client)
        self.cards = CardPublisher(self.client, self.settings)
        self.capture_started_at = None
        self.bootstrapping = True

    def close(self):
        self.cards.close()

    async def _enable_capture(self, connection):
        deadline = time.monotonic() + 30
        while True:
            try:
                async with connection.transaction():
                    state = await (
                        await connection.execute(
                            "SELECT enabled FROM analytics_pipeline_state "
                            "WHERE singleton FOR UPDATE NOWAIT"
                        )
                    ).fetchone()
                    if not state["enabled"]:
                        # Activation waits for prior writers and excludes new ones.
                        # NOWAIT + transaction rollback releases every partial lock
                        # before retry, avoiding cross-table writer deadlocks.
                        names = sql.SQL(", ").join(sql.Identifier(name) for name in sorted(TABLES))
                        await connection.execute(
                            sql.SQL("LOCK TABLE {} IN SHARE MODE NOWAIT").format(names)
                        )
                        await connection.execute(
                            "UPDATE analytics_pipeline_state SET enabled = true, "
                            "capture_started_at = clock_timestamp() WHERE singleton"
                        )
                        for name in BACKFILL_TABLES:
                            await connection.execute(
                                "INSERT INTO analytics_bootstrap_cursors(source_table) VALUES (%s) "
                                "ON CONFLICT DO NOTHING",
                                (name,),
                            )
                return
            except LockNotAvailable:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Analytics capture activation is waiting for source writers"
                    ) from None
                await asyncio.sleep(0.1)

    async def _lease(self, connection):
        async with connection.transaction():
            row = await (
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET lease_token = %s, "
                    "lease_generation = nextval('analytics_export_generation'), "
                    "lease_until = clock_timestamp() + make_interval(secs => %s) "
                    "WHERE singleton AND enabled AND (lease_until IS NULL "
                    "OR lease_until < clock_timestamp() OR lease_token = %s) "
                    "RETURNING capture_started_at, lease_generation, native_capture_started_at",
                    (
                        self.token,
                        max(
                            LEASE_SECONDS,
                            math.ceil(
                                max(
                                    self.settings.clickhouse_timeout_seconds,
                                    self.settings.clickhouse_max_execution_seconds,
                                )
                            )
                            + 30,
                        ),
                        self.token,
                    ),
                )
            ).fetchone()
        return row

    async def _check_lease(self, connection):
        row = await (
            await connection.execute(
                "SELECT 1 FROM analytics_pipeline_state WHERE singleton AND lease_token = %s "
                "AND lease_until > clock_timestamp() FOR UPDATE",
                (self.token,),
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("Analytics publisher lease was superseded")

    async def _renew_lease(self, connection):
        lease = await self._lease(connection)
        if lease is None:
            raise RuntimeError("Analytics publisher lease was superseded")
        return lease

    async def _backfill_one(self, connection, generation):
        # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Retire existing-population PG
        # import after cutover and safe fresh-install capture ordering are proven.
        # Native card policy rebuilds and ongoing captured-event delivery remain.
        async with connection.transaction():
            row = await (
                await connection.execute(
                    "SELECT source_table,cursor FROM analytics_bootstrap_cursors "
                    "WHERE NOT complete ORDER BY source_table LIMIT 1"
                )
            ).fetchone()
            if row is None:
                return False
            if row["source_table"] == "overview_daily":
                assert self.capture_started_at is not None
                records = await (
                    await connection.execute(
                        "SELECT day,jsonb_build_object('opens',metrics->'opens',"
                        "'readers',metrics->'readers','accounts',metrics->'accounts',"
                        "'published',metrics->'published') AS metrics FROM overview_daily "
                        "WHERE day<%s AND (%s::date IS NULL OR day>%s::date) "
                        "ORDER BY day LIMIT %s",
                        (
                            self.capture_started_at.date(),
                            row["cursor"]["day"] if row["cursor"] else None,
                            row["cursor"]["day"] if row["cursor"] else None,
                            self.settings.analytics_backfill_batch_size,
                        ),
                    )
                ).fetchall()
            else:
                table = TABLES[row["source_table"]]
                keys = sql.SQL(", ").join(sql.Identifier(name) for name in table.keys)
                where: sql.Composable = sql.SQL("")
                parameters = []
                if row["cursor"] is not None:
                    source_types = Base.metadata.tables[table.name].columns
                    bindings = []
                    for name in table.keys:
                        kind = source_types[name].type.compile(dialect=connection_dialect())
                        bindings.append(sql.SQL("%s::" + str(kind)))
                        parameters.append(row["cursor"][name])
                    where = sql.SQL(" WHERE ({}) > ({})").format(keys, sql.SQL(", ").join(bindings))
                parameters.append(self.settings.analytics_backfill_batch_size)
                query = sql.SQL("SELECT {} AS record FROM {} AS source{}").format(
                    sql.SQL(table.record_sql("source")),
                    sql.Identifier(table.name),
                    where,
                ) + sql.SQL(" ORDER BY {} LIMIT %s").format(keys)
                records = await (await connection.execute(query, parameters)).fetchall()
        if row["source_table"] == "overview_daily":
            await self.upgrade._archive_days(records, self.capture_started_at, version=generation)
            cursor = {"day": records[-1]["day"].isoformat()} if records else row["cursor"]
            complete = len(records) < self.settings.analytics_backfill_batch_size
            async with connection.transaction():
                await self._check_lease(connection)
                await connection.execute(
                    "UPDATE analytics_bootstrap_cursors SET cursor=%s,complete=%s "
                    "WHERE source_table=%s",
                    (Jsonb(cursor), complete, "overview_daily"),
                )
            return True
        events = [
            dict(
                id=0,
                source_table=table.name,
                operation="I",
                record=item["record"],
                before_record=None,
                created_at=self.capture_started_at,
            )
            for item in records
        ]
        facts = await transform_events(connection, events, 1)
        await self.client.insert("analytics_fact_events", facts)
        await self.gauges.ingest(connection, events, generation=0)
        complete = len(records) < self.settings.analytics_backfill_batch_size
        cursor = (
            {key: records[-1]["record"][key] for key in table.keys} if records else row["cursor"]
        )
        async with connection.transaction():
            await self._check_lease(connection)
            await self.rollups.mark_dirty(connection, facts, generation)
            await self.gauges.mark_dirty(connection, events)
            await connection.execute(
                "UPDATE analytics_bootstrap_cursors SET cursor=%s,complete=%s "
                "WHERE source_table=%s",
                (Jsonb(cursor), complete, table.name),
            )
        return True

    async def _export_events(self, connection, generation):
        async with connection.transaction():
            events = await (
                await connection.execute(
                    "SELECT id,source_table,operation,record,before_record,"
                    "created_at,format_version "
                    "FROM analytics_ingest_events ORDER BY id LIMIT %s",
                    (self.settings.analytics_batch_size,),
                )
            ).fetchall()
        if not events:
            return 0
        captured = events
        # Fail closed before any acknowledgement, including old-format
        # reconciliation. A future producer cannot silently lose its changes.
        for event in captured:
            # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Accept only V3 after V1/V2
            # queues have drained and the supported upgrade floor advances.
            if event["format_version"] not in {1, 2, 3}:
                raise ValueError("Unsupported analytical capture format")
            sources = set(TABLES) | set(V1_KEYS) if event["format_version"] == 1 else TABLES
            if event["source_table"] not in sources:
                raise ValueError("Unsupported analytical capture source")
        events = await self.upgrade.normalize_pending(connection, captured, generation)
        facts = await transform_events(connection, events, generation)
        await self.client.insert("analytics_fact_events", facts)
        await self.gauges.ingest(connection, events, generation=generation)
        await self.cards.ingest(connection, captured, generation, fence=self._check_lease)
        async with connection.transaction():
            await self._check_lease(connection)
            await self.rollups.mark_dirty(connection, facts, generation)
            await self.gauges.mark_dirty(connection, events)
            await connection.execute(
                "DELETE FROM analytics_ingest_events WHERE id=ANY(%s)",
                ([event["id"] for event in captured],),
            )
        return len(captured)

    @staticmethod
    def _raw_complete(state):
        return (
            state["sources_complete"]
            and state["native_import_complete"]
            and state["legacy_complete"]
        )

    async def _raw_backfill_complete(self, connection):
        state = await (
            await connection.execute(
                "SELECT native_import_complete," + RAW_COVERAGE + " "
                "FROM analytics_pipeline_state WHERE singleton"
            )
        ).fetchone()
        return self._raw_complete(state)

    async def _publish_status(self, connection, lease, observed_at):
        async with connection.transaction():
            await self._check_lease(connection)
            state = await (
                await connection.execute(
                    "SELECT *," + RAW_COVERAGE + " "
                    "FROM analytics_pipeline_state WHERE singleton FOR UPDATE"
                )
            ).fetchone()
            raw_complete = self._raw_complete(state)
            if raw_complete and not state["native_bootstrap_barrier"]:
                await connection.execute(
                    "UPDATE analytics_fact_refresh SET initial_pending=true,"
                    "available_at=clock_timestamp()"
                )
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET "
                    "native_bootstrap_barrier=true WHERE singleton"
                )
        gauge_complete = await self.gauges.bootstrap_complete(connection) if raw_complete else False
        async with connection.transaction():
            await self._check_lease(connection)
            state = await (
                await connection.execute(
                    "SELECT *,NOT EXISTS(SELECT 1 FROM analytics_fact_refresh "
                    "WHERE initial_pending) "
                    "AS facts_complete,least((SELECT min(created_at) FROM "
                    "analytics_ingest_events), "
                    "(SELECT min(requested_at) FROM analytics_fact_refresh), "
                    "(SELECT min(available_at) FROM analytics_gauge_refreshes "
                    "WHERE available_at<=clock_timestamp())) AS oldest "
                    "FROM analytics_pipeline_state WHERE singleton FOR UPDATE"
                )
            ).fetchone()
            ready = bool(
                state["native_bootstrap_complete"]
                or (
                    state["native_bootstrap_barrier"] and state["facts_complete"] and gauge_complete
                )
            )
            if ready and not state["native_bootstrap_complete"]:
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET "
                    "native_bootstrap_complete=true WHERE singleton"
                )
        self.bootstrapping = not ready or self.cards.bootstrapping
        if ready == self._last_ready and time.monotonic() - self._last_status < 30:
            return
        updated_at = min(observed_at, state["oldest"]) if state["oldest"] else observed_at
        capture = state["native_capture_started_at"] or state["capture_started_at"]
        coverage = state["reader_coverage_start"] or (capture - timedelta(days=29)).date()
        await self.client.insert(
            "analytics_pipeline_status",
            [
                {
                    "version": lease["lease_generation"],
                    "ready": int(ready),
                    "updated_at": updated_at,
                    "capture_started_at": capture,
                    "reader_coverage_start": coverage,
                    "lag_seconds": max((datetime.now(UTC) - updated_at).total_seconds(), 0),
                }
            ],
        )
        self._last_status = time.monotonic()
        self._last_ready = ready

    async def _refresh_gauges(self, connection):
        deadline = time.monotonic() + GAUGE_CYCLE_SECONDS
        refreshed = False
        for _ in range(GAUGE_BATCH_SIZE):
            lease = await self._renew_lease(connection)
            if not await self.gauges.refresh_one(connection, lease["lease_generation"]):
                break
            refreshed = True
            if time.monotonic() >= deadline:
                break
        return refreshed

    async def _publish_views(self, connection, generation):
        published = await self.rollups.refresh_one(connection, generation, fence=self._check_lease)
        gauges = await self._refresh_gauges(connection)
        lease = await self._renew_lease(connection)
        cards = await self.cards.once(
            connection,
            lease["lease_generation"],
            fence=self._check_lease,
            renew=self._lease,
        )
        lease = await self._renew_lease(connection)
        compacted = await self.rollups.compact_one(
            connection, fence=self._check_lease, renew=self._lease
        )
        return lease, published or gauges or cards or compacted

    async def once(self):
        async with await psycopg.AsyncConnection.connect(
            postgres_dsn(self.settings),
            row_factory=dict_row,
            connect_timeout=self.settings.database_connect_timeout_seconds,
            autocommit=True,
        ) as connection:
            await connection.execute("SET statement_timeout = '10s'")
            await self._enable_capture(connection)
            lease = await self._lease(connection)
            if lease is None:
                return False
            self.capture_started_at = (
                lease["native_capture_started_at"] or lease["capture_started_at"]
            )
            observed_at = datetime.now(UTC)
            # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Delete finite cutover work
            # and its readiness fields together; preserve ongoing native work.
            upgrading = await self.upgrade.once(
                connection, lease["lease_generation"], fence=self._check_lease
            )
            lease = await self._renew_lease(connection)
            self.capture_started_at = (
                lease["native_capture_started_at"] or lease["capture_started_at"]
            )
            backfilling = await self._backfill_one(connection, lease["lease_generation"])
            lease = await self._renew_lease(connection)
            generation = lease["lease_generation"]
            count = await self._export_events(connection, generation)
            # Initial reports cannot be ready before every raw source and legacy
            # import is durable. Retain dirty cohorts while importing, rather
            # than rebuilding partial history after each bounded source page.
            raw_complete = await self._raw_backfill_complete(connection)
            refreshed = False
            if raw_complete:
                lease, refreshed = await self._publish_views(connection, generation)
            else:
                lease = await self._renew_lease(connection)
            await self._publish_status(connection, lease, observed_at)
            async with connection.transaction():
                await self._check_lease(connection)
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET last_success_at=clock_timestamp(), "
                    "lease_token=NULL,lease_until=NULL WHERE singleton"
                )
        write_runtime_marker(runtime_marker("analytics-worker"), str(time.time()))
        return upgrading or backfilling or count > 0 or refreshed


def connection_dialect():
    from sqlalchemy.dialects.postgresql import dialect

    return dialect()


async def record_backlog(settings):
    """Sample indexed queue age without counting or loading the pending history."""
    runtime = current()
    if runtime is None:
        return
    async with await psycopg.AsyncConnection.connect(
        postgres_dsn(settings),
        row_factory=dict_row,
        connect_timeout=settings.database_connect_timeout_seconds,
        autocommit=True,
    ) as connection:
        await connection.execute("SET statement_timeout = '2s'")
        row = await (
            await connection.execute(
                "SELECT min(created_at) AS oldest FROM analytics_ingest_events"
            )
        ).fetchone()
    assert row is not None  # An aggregate without GROUP BY always returns one row.
    age = max(0.0, (datetime.now(UTC) - row["oldest"]).total_seconds()) if row["oldest"] else 0.0
    runtime.instruments["analytics_oldest"].set(age, {"kind": "analytics"})


async def pause(stopping, delay):
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stopping.wait(), timeout=delay)


def _poll_delay(settings, *, busy, bootstrapping, failures):
    delay = (
        settings.analytics_backfill_poll_seconds
        if busy and bootstrapping
        else settings.analytics_poll_seconds
    )
    return max(delay, min(60, 2 ** min(failures, 6))) if failures else delay


async def _sample_backlog(settings):
    try:
        await record_backlog(settings)
    except Exception:
        # Metrics failures cannot prevent durable fact delivery.
        logger.warning("analytics_backlog_sample_failed")


async def run(once=False, initialize=False):
    settings = get_settings()
    async with AnalyticsClient(settings) as client:
        if initialize:
            await client.initialize()
            return
        exporter = AnalyticsExporter(client, settings)
        failures = 0
        initialized = False
        last_sample = None
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        for item in signals:
            loop.add_signal_handler(item, stopping.set)
        try:
            while not stopping.is_set():
                sampled_at = time.monotonic()
                if last_sample is None or sampled_at - last_sample >= 30:
                    last_sample = sampled_at
                    await _sample_backlog(settings)
                try:
                    with background_cycle("analytics.export"):
                        if not initialized:
                            await client.initialize()
                            initialized = True
                        busy = await exporter.once()
                    failures = 0
                except Exception:
                    logger.exception("analytics_export_failed")
                    if once:
                        raise
                    failures += 1
                    busy = False
                if once:
                    return
                await pause(
                    stopping,
                    _poll_delay(
                        settings,
                        busy=busy,
                        bootstrapping=exporter.bootstrapping,
                        failures=failures,
                    ),
                )
        finally:
            for item in signals:
                loop.remove_signal_handler(item)
            exporter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        marker = runtime_marker("analytics-worker")
        if not marker.is_file() or time.time() - float(marker.read_text()) > LEASE_SECONDS:
            raise SystemExit(1)
        return
    settings = get_settings()
    configure_logging("analytics-worker", settings.log_level, settings.log_format)
    telemetry = start_runtime("analytics-worker")
    try:
        asyncio.run(run(args.once, args.initialize))
    finally:
        stop_runtime(telemetry)


if __name__ == "__main__":
    main()
