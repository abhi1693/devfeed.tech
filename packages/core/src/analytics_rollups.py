"""Rebuild only dirty fact cohorts and atomically publish chart generations."""

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_facts import PAYLOAD_NAMES, dirty_buckets

BUCKET = "{bucket:DateTime64(6, 'UTC')}"
END = "{end:DateTime64(6, 'UTC')}"
GENERATION = "{generation:UInt64}"
BUCKETS = "{buckets:Array(DateTime('UTC'))}"
REFRESH_BATCH_SIZE = 32
DAILY_REFRESH_BATCH_SIZE = 1
CANONICAL_READ_BLOCK_SIZE = 1024


@dataclass(frozen=True)
class Rollup:
    table: str
    dimensions: tuple[str, ...]
    measures: str
    array_join: str = ""


ROLLUPS = {
    "publishing": Rollup(
        "publishing_hourly",
        (),
        "sum(published), sum(automated), "
        "quantilesTDigestStateIf(0.5, 0.95)(delay_seconds, has_delay = 1)",
    ),
    "audience": Rollup(
        "audience_hourly",
        (),
        "sum(opens), sum(accounts_created), uniqCombined64StateIf(viewer_key, opens > 0)",
    ),
    "processing": Rollup(
        "processing_hourly",
        ("task", "status", "outcome"),
        "sum(completed), sum(failed), sum(useful), "
        "quantilesTDigestStateIf(0.5, 0.95)(toFloat64(duration_ms) / 1000, has_duration = 1)",
    ),
    "inference": Rollup(
        "inference_hourly",
        ("task", "model", "effort", "status"),
        "sum(calls), sum(failed), sum(unreported), sum(web_searches), "
        "sum(input_tokens), sum(cached_input_tokens), sum(output_tokens), "
        "sum(reasoning_tokens), "
        "quantilesTDigestStateIf(0.5, 0.95)(toFloat64(duration_ms), has_duration = 1)",
    ),
    "sources": Rollup("source_daily", ("source_id",), "sum(published)", "source_ids AS source_id"),
    "topics": Rollup("topic_daily", ("topic_id",), "sum(published)", "topic_ids AS topic_id"),
}
COMPACTION_DOMAINS = {rollup.table: domain for domain, rollup in ROLLUPS.items()}

STAGING_PATTERN = "^analytics_compact_(" + "|".join(COMPACTION_DOMAINS) + ")_[0-9a-f]{32}$"
ORPHAN_CHECK_SECONDS = 300
ORPHAN_MIN_AGE_SECONDS = 600
COMPACTION_POLL_SECONDS = 30
COMPACTION_GRACE_MARGIN_SECONDS = 5
COMPACTION_WARNING_SECONDS = 300
MAX_MONTH_BUCKETS = 31 * 24
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Compaction:
    table: str
    month: int
    publishes: int
    domain: str
    bounds: tuple[tuple[str, int], ...]
    previous_token: str | None
    marker: str
    ready_at: float
    next_check: float
    submitted_at: float | None = None
    next_warning: float = 0


def canonical_sql(domain, *, daily=False, batch=False):
    selected = ", ".join(
        f"tupleElement(fact, {position}) AS `{name}`"
        for position, name in enumerate(PAYLOAD_NAMES, 1)
    )
    window = f"bucket_hour >= {BUCKET} AND bucket_hour < {END}"
    if batch:
        bucket = "toStartOfDay(bucket_hour)" if daily else "bucket_hour"
        window = f"{bucket} IN {BUCKETS}"
    # The storage sorting key is exactly (domain, bucket_hour, fact_id), so FINAL
    # can merge argMax states in order instead of retaining a second aggregate
    # state for every fact. Bound wide state deserialization independently of the
    # number of dirty buckets. Only sorting-key columns belong in PREWHERE:
    # filtering deleted payloads before the merge would resurrect old versions.
    return (
        f"SELECT bucket_hour, fact_id, {selected} FROM ("
        "SELECT bucket_hour, fact_id, finalizeAggregation(state) AS fact "
        f"FROM analytics_fact_state FINAL PREWHERE domain = '{domain}' AND {window} "
        f"SETTINGS max_block_size={CANONICAL_READ_BLOCK_SIZE}) "
        "WHERE tupleElement(fact, 1) = 0"
    )


def rebuild_sql(domain, *, batch=False):
    if domain not in ROLLUPS:
        raise ValueError("Unknown analytical rollup domain")
    rollup = ROLLUPS[domain]
    daily = bool(rollup.array_join)
    source = canonical_sql("publishing" if daily else domain, daily=daily, batch=batch)
    bucket = "bucket_hour" if batch else BUCKET
    if daily:
        bucket = f"toDate({bucket})"
    selected = ", ".join((bucket, GENERATION, *rollup.dimensions, rollup.measures))
    grouped = ", ".join((bucket, *rollup.dimensions) if batch else rollup.dimensions)
    return (
        f"INSERT INTO analytics_{rollup.table} WITH facts AS ({source}) "
        f"SELECT {selected} FROM facts"
        + (f" ARRAY JOIN {rollup.array_join}" if daily else "")
        + (f" GROUP BY {grouped}" if grouped else "")
    )


class RollupCompactor:
    """Manage generation-safe cleanup and its retry state."""

    def __init__(self, client):
        self.client = client
        self._next_orphan_check = 0.0
        self._next_compaction_check = 0.0
        self._compactions: dict[str, _Compaction] = {}
        self._compaction_blocked: dict[tuple[str, int], float] = {}
        self._compaction_turn = 0
        self._compaction_grace = (
            max(
                # Reader clients can independently use the configured maximum
                # 300-second timeout, including admission and connection.
                300,
                client.settings.clickhouse_timeout_seconds,
                client.settings.clickhouse_max_execution_seconds,
            )
            + COMPACTION_GRACE_MARGIN_SECONDS
        )

    async def _cleanup_one(self, connection, *, fence, renew):
        # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Retire this old staging-table
        # cleanup after every supported installation has quiesced old exporters
        # and removed their orphaned analytics_compact_* tables. Keep the native
        # generation-bounded pruning below.
        now = time.monotonic()
        if now < self._next_orphan_check:
            return False
        # Advance before I/O so an unavailable server cannot cause a catalog
        # scan on every exporter cycle. The catalog query has its own short cap.
        self._next_orphan_check = now + ORPHAN_CHECK_SECONDS
        rows = await self.client.query(
            "SELECT name,engine,dateDiff('second',metadata_modification_time,now()) AS age_seconds "
            "FROM system.tables WHERE database=currentDatabase() "
            "AND engine='MergeTree' AND match(name,{pattern:String}) "
            "AND metadata_modification_time<=now()-toIntervalSecond({age:UInt32}) "
            "ORDER BY metadata_modification_time,name LIMIT 1 SETTINGS max_execution_time=1",
            {"pattern": STAGING_PATTERN, "age": ORPHAN_MIN_AGE_SECONDS},
        )
        if not rows:
            return False
        row = rows[0]
        if (
            re.fullmatch(STAGING_PATTERN, row["name"]) is None
            or row["engine"] != "MergeTree"
            or row["age_seconds"] < ORPHAN_MIN_AGE_SECONDS
        ):
            return False
        if await renew(connection) is None:
            raise RuntimeError("Analytics compactor lease was superseded")
        async with connection.transaction():
            await fence(connection)
        await self.client.execute("DROP TABLE IF EXISTS " + row["name"])
        return True

    async def compact_one(self, connection, *, fence, renew):
        cleaned = await self._cleanup_one(connection, fence=fence, renew=renew)
        now = time.monotonic()
        due = sorted(
            (plan for plan in self._compactions.values() if plan.next_check <= now),
            key=lambda plan: (plan.next_check, plan.table),
        )
        if due:
            return await self._advance_compaction(connection, due[0], now, fence, renew) or cleaned
        if len(self._compactions) >= len(COMPACTION_DOMAINS) or now < self._next_compaction_check:
            return cleaned
        try:
            plan = await self._prepare_compaction(connection, now)
        except (AnalyticsUnavailable, ValueError, KeyError) as error:
            logger.warning("analytics_compaction_prepare_failed: %s", error)
            self._next_compaction_check = now + COMPACTION_POLL_SECONDS
            return cleaned
        if plan is not None:
            self._compactions[plan.table] = plan
        return cleaned

    async def _prepare_compaction(self, connection, now):
        self._compaction_blocked = {
            key: until for key, until in self._compaction_blocked.items() if until > now
        }
        excluded = list(self._compactions)
        blocked = list(self._compaction_blocked)
        tables = list(COMPACTION_DOMAINS)
        async with connection.transaction():
            row = await (
                await connection.execute(
                    "SELECT table_name,month,publishes,last_prune_token "
                    "FROM analytics_rollup_compaction "
                    "WHERE publishes>=16 AND NOT (table_name=ANY(%s::text[])) "
                    "AND NOT EXISTS(SELECT 1 FROM unnest(%s::text[],%s::integer[]) "
                    "AS b(table_name,month) WHERE "
                    "b.table_name=analytics_rollup_compaction.table_name "
                    "AND b.month=analytics_rollup_compaction.month) "
                    "ORDER BY mod(array_position(%s::text[],table_name)-1-%s+%s,%s),"
                    "publishes DESC,table_name,month LIMIT 1",
                    (
                        excluded,
                        [item[0] for item in blocked],
                        [item[1] for item in blocked],
                        tables,
                        self._compaction_turn,
                        len(tables),
                        len(tables),
                    ),
                )
            ).fetchone()
        if row is None:
            self._next_compaction_check = now + COMPACTION_POLL_SECONDS
            return None
        table = row["table_name"]
        self._compaction_turn = (tables.index(table) + 1) % len(tables)
        # Failures to read this month's manifest do not starve other months.
        key = (table, int(row["month"]))
        self._compaction_blocked[key] = now + COMPACTION_POLL_SECONDS
        bounds = await self._compaction_bounds(row)
        if not bounds:
            # An absent manifest cannot authorize deleting data or acknowledging
            # maintenance work. Retry slowly while other exporter work continues.
            logger.warning(
                "analytics_compaction_manifest_missing: %s %s", row["table_name"], row["month"]
            )
            return None
        self._compaction_blocked.pop(key)
        domain = COMPACTION_DOMAINS[table]
        previous = row["last_prune_token"]
        identity = repr((row["table_name"], row["month"], row["publishes"], bounds, previous))
        marker = "devfeed_prune_" + hashlib.sha256(identity.encode()).hexdigest()
        ready_at = now + self._compaction_grace
        return _Compaction(
            row["table_name"],
            row["month"],
            row["publishes"],
            domain,
            bounds,
            previous,
            marker,
            ready_at,
            ready_at,
        )

    async def _compaction_bounds(self, row):
        table = row["table_name"]
        domain = COMPACTION_DOMAINS[table]
        year, month = divmod(int(row["month"]), 100)
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=UTC)
        rows = await self.client.query(
            "SELECT bucket,generation FROM analytics_bucket_manifest FINAL "
            "PREWHERE domain={domain:String} AND bucket>={start:DateTime('UTC')} "
            "AND bucket<{end:DateTime('UTC')} ORDER BY bucket LIMIT 745",
            {
                "domain": domain,
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        if len(rows) > MAX_MONTH_BUCKETS:
            raise ValueError("Too many rollup buckets in one month")
        bounds = []
        for item in rows:
            value = item["bucket"]
            value = (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                if isinstance(value, str)
                else value
            )
            value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            generation = int(item["generation"])
            if not (start <= value < end and 0 <= generation < 2**64):
                raise ValueError("Invalid rollup bucket or generation")
            if (
                value.minute
                or value.second
                or value.microsecond
                or (domain in {"sources", "topics"} and value.hour)
            ):
                raise ValueError("Invalid rollup bucket alignment")
            bounds.append((value.strftime("%Y-%m-%d %H:%M:%S"), generation))
        if len({bucket for bucket, _ in bounds}) != len(bounds):
            raise ValueError("Duplicate rollup bucket manifests")
        return tuple(bounds)

    @staticmethod
    def _compaction_sql(plan):
        daily = plan.domain in {"sources", "topics"}
        field = "bucket_day" if daily else "bucket_hour"
        buckets = [
            f"toDate('{bucket[:10]}')" if daily else f"toDateTime('{bucket}','UTC')"
            for bucket, _ in plan.bounds
        ]
        generations = [f"toUInt64({generation})" for _, generation in plan.bounds]
        # Constant transform lookup avoids checking up to 744 OR branches for
        # every row. Unknown buckets map to zero and are therefore retained.
        return (
            f"ALTER TABLE analytics_{plan.table} DELETE WHERE toYYYYMM({field})={{month:UInt32}} "
            f"AND generation<transform({field},["
            + ",".join(buckets)
            + "],["
            + ",".join(generations)
            + "],toUInt64(0)) AND {marker:String}!=''"
        )

    async def _mutation_state(self, plan):
        rows = await self.client.query(
            "SELECT position(command,{marker:String})>0 AS ours,is_done,"
            "left(latest_fail_reason,512) AS latest_fail_reason FROM system.mutations "
            "WHERE database=currentDatabase() AND table={table:String} "
            "AND (is_done=0 OR position(command,{marker:String})>0) "
            "ORDER BY is_done,create_time DESC LIMIT 128",
            {"table": "analytics_" + plan.table, "marker": plan.marker},
        )
        pending = [row for row in rows if not row["is_done"]]
        complete = any(row["is_done"] and row["ours"] for row in rows)
        return pending, complete

    async def _advance_compaction(self, connection, plan, now, fence, renew):
        plan.next_check = now + COMPACTION_POLL_SECONDS
        try:
            pending, complete = await self._mutation_state(plan)
        except AnalyticsUnavailable as error:
            logger.warning("analytics_compaction_poll_failed: %s", error)
            return False
        if pending:
            failed = next(
                (item["latest_fail_reason"] for item in pending if item["latest_fail_reason"]), None
            )
            if failed and now >= plan.next_warning:
                logger.warning("analytics_compaction_mutation_waiting: %s", failed)
                plan.next_warning = now + COMPACTION_WARNING_SECONDS
            return False
        if complete:
            await self._ack_compaction(connection, plan, fence)
            self._compactions.pop(plan.table, None)
            return True
        if plan.submitted_at is not None and now - plan.submitted_at < self._compaction_grace:
            return False
        if await renew(connection) is None:
            raise RuntimeError("Analytics compactor lease was superseded")
        async with connection.transaction():
            await fence(connection)
        plan.submitted_at = now
        try:
            await self.client.execute(
                self._compaction_sql(plan), {"month": plan.month, "marker": plan.marker}
            )
        except AnalyticsUnavailable as error:
            # The server may have accepted the mutation before the response was
            # lost. Keep the marker and poll before ever retrying the request.
            logger.warning("analytics_compaction_submit_uncertain: %s", error)
            return False
        return True

    async def _ack_compaction(self, connection, plan, fence):
        async with connection.transaction():
            await fence(connection)
            await connection.execute(
                "UPDATE analytics_rollup_compaction SET publishes=greatest(publishes-%s,0), "
                "last_prune_token=%s WHERE table_name=%s AND month=%s "
                "AND last_prune_token IS NOT DISTINCT FROM %s",
                (plan.publishes, plan.marker, plan.table, plan.month, plan.previous_token),
            )


class RollupPublisher:
    """Publish dirty cohorts and delegate their cleanup to a compactor."""

    def __init__(self, client):
        self.client = client
        self.interval = client.settings.analytics_rollup_interval_seconds
        self.compactor = RollupCompactor(client)

    async def mark_dirty(self, connection, rows, generation):
        buckets = dirty_buckets(rows, generation)
        if not buckets:
            return
        await connection.execute(
            "INSERT INTO analytics_fact_refresh"
            "(domain,bucket,generation,requested_at,available_at) "
            "SELECT domain,bucket,%s,clock_timestamp(),"
            "clock_timestamp()+make_interval(secs=>%s) "
            "FROM unnest(%s::text[],%s::timestamptz[]) AS dirty(domain,bucket) "
            "ON CONFLICT(domain,bucket) "
            "DO UPDATE SET generation=greatest(analytics_fact_refresh.generation, "
            "excluded.generation),requested_at=least(analytics_fact_refresh.requested_at, "
            "excluded.requested_at)",
            (
                generation,
                self.interval,
                [bucket["domain"] for bucket in buckets],
                [bucket["bucket"] for bucket in buckets],
            ),
        )

    async def refresh_one(self, connection, generation, *, fence):
        async with connection.transaction():
            rows = await (
                await connection.execute(
                    "WITH oldest AS (SELECT domain "
                    "FROM analytics_fact_refresh WHERE available_at<=%s "
                    "ORDER BY available_at,requested_at,domain,bucket LIMIT 1) "
                    "SELECT domain,bucket,generation FROM analytics_fact_refresh "
                    "WHERE available_at<=%s AND domain=(SELECT domain FROM oldest) "
                    "ORDER BY available_at,requested_at,domain,bucket LIMIT "
                    "(SELECT CASE WHEN domain IN ('sources','topics') THEN %s ELSE %s END "
                    "FROM oldest)",
                    (
                        datetime.now(UTC),
                        datetime.now(UTC),
                        DAILY_REFRESH_BATCH_SIZE,
                        REFRESH_BATCH_SIZE,
                    ),
                )
            ).fetchall()
        if not rows:
            return False
        domain = rows[0]["domain"]
        parameters = {
            "buckets": [
                row["bucket"].astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") for row in rows
            ],
            "generation": generation,
        }
        await self.client.execute(
            rebuild_sql(domain, batch=True) + " SETTINGS optimize_aggregation_in_order=1",
            parameters,
        )
        # A manifest is the commit record. Empty rebuilt groups still publish a
        # generation, hiding every obsolete group from a previous generation.
        async with connection.transaction():
            await fence(connection)
        await self.client.insert(
            "analytics_bucket_manifest",
            [
                {
                    "domain": domain,
                    "bucket": row["bucket"],
                    "generation": generation,
                    "updated_at": datetime.now(UTC),
                    "lag_seconds": 0,
                }
                for row in rows
            ],
        )
        async with connection.transaction():
            await fence(connection)
            for row in rows:
                await connection.execute(
                    "DELETE FROM analytics_fact_refresh WHERE domain=%s AND bucket=%s "
                    "AND generation<=%s",
                    (domain, row["bucket"], row["generation"]),
                )
        table = ROLLUPS[domain].table
        async with connection.transaction():
            await fence(connection)
            for month in sorted({row["bucket"].year * 100 + row["bucket"].month for row in rows}):
                await connection.execute(
                    "INSERT INTO analytics_rollup_compaction(table_name,month,publishes) "
                    "VALUES(%s,%s,1) "
                    "ON CONFLICT(table_name,month) DO UPDATE SET "
                    "publishes=analytics_rollup_compaction.publishes+1",
                    (table, month),
                )
        return True

    async def compact_one(self, connection, *, fence, renew):
        return await self.compactor.compact_one(connection, fence=fence, renew=renew)
