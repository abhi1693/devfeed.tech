"""Rebuild only dirty fact cohorts and atomically publish chart generations."""

import re
import time
import uuid
from datetime import UTC, datetime

from devfeed_core.analytics_facts import PAYLOAD_NAMES, dirty_buckets

BUCKET = "{bucket:DateTime64(6, 'UTC')}"
END = "{end:DateTime64(6, 'UTC')}"
GENERATION = "{generation:UInt64}"
BUCKETS = "{buckets:Array(DateTime('UTC'))}"
REFRESH_BATCH_SIZE = 32
DAILY_REFRESH_BATCH_SIZE = 1
CANONICAL_READ_BLOCK_SIZE = 1024
COMPACTION_DOMAINS = {
    "publishing_hourly": "publishing",
    "audience_hourly": "audience",
    "processing_hourly": "processing",
    "inference_hourly": "inference",
    "source_daily": "sources",
    "topic_daily": "topics",
}
STAGING_PATTERN = "^analytics_compact_(" + "|".join(COMPACTION_DOMAINS) + ")_[0-9a-f]{32}$"
ORPHAN_CHECK_SECONDS = 300
ORPHAN_MIN_AGE_SECONDS = 600


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
    daily = domain in {"sources", "topics"}
    source = canonical_sql("publishing" if daily else domain, daily=daily, batch=batch)
    prefix = f"WITH facts AS ({source}) "
    bucket = "bucket_hour" if batch else BUCKET
    grouped = f" GROUP BY {bucket}" if batch else ""
    if domain == "publishing":
        return (
            "INSERT INTO analytics_publishing_hourly "
            + prefix
            + (
                "SELECT "
                f"{bucket}, {GENERATION}, sum(published), sum(automated), "
                "quantilesTDigestStateIf(0.5, 0.95)(delay_seconds, has_delay = 1) FROM facts"
                + grouped
            )
        )
    if domain == "audience":
        return (
            "INSERT INTO analytics_audience_hourly "
            + prefix
            + (
                "SELECT "
                f"{bucket}, {GENERATION}, sum(opens), sum(accounts_created), "
                "uniqCombined64StateIf(viewer_key, opens > 0) FROM facts" + grouped
            )
        )
    if domain == "processing":
        return (
            "INSERT INTO analytics_processing_hourly "
            + prefix
            + (
                "SELECT "
                f"{bucket}, {GENERATION}, task, status, outcome, sum(completed), sum(failed), "
                "sum(useful), quantilesTDigestStateIf(0.5, 0.95)"
                "(toFloat64(duration_ms) / 1000, has_duration = 1) "
                f"FROM facts GROUP BY {'bucket_hour, ' if batch else ''}task, status, outcome"
            )
        )
    if domain == "inference":
        return (
            "INSERT INTO analytics_inference_hourly "
            + prefix
            + (
                "SELECT "
                f"{bucket}, {GENERATION}, task, model, effort, status, "
                "sum(calls), sum(failed), sum(unreported), sum(web_searches), "
                "sum(input_tokens), sum(cached_input_tokens), sum(output_tokens), "
                "sum(reasoning_tokens), quantilesTDigestStateIf(0.5, 0.95)"
                "(toFloat64(duration_ms), has_duration = 1) "
                f"FROM facts GROUP BY {'bucket_hour, ' if batch else ''}task, model, effort, status"
            )
        )
    if domain in {"sources", "topics"}:
        singular = "source" if domain == "sources" else "topic"
        return (
            f"INSERT INTO analytics_{singular}_daily "
            + prefix
            + (
                f"SELECT toDate({bucket}), {GENERATION}, "
                f"{singular}_id, sum(published) FROM facts "
                f"ARRAY JOIN {singular}_ids AS {singular}_id GROUP BY "
                f"{'toDate(bucket_hour), ' if batch else ''}{singular}_id"
            )
        )
    raise ValueError("Unknown analytical rollup domain")


class RollupPublisher:
    def __init__(self, client):
        self.client = client
        self.interval = client.settings.analytics_rollup_interval_seconds
        self._next_orphan_check = 0.0

    async def _cleanup_one(self, connection, *, fence, renew):
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
        table = {
            "publishing": "publishing_hourly",
            "audience": "audience_hourly",
            "processing": "processing_hourly",
            "inference": "inference_hourly",
            "sources": "source_daily",
            "topics": "topic_daily",
        }[domain]
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
        cleaned = await self._cleanup_one(connection, fence=fence, renew=renew)
        async with connection.transaction():
            row = await (
                await connection.execute(
                    "SELECT table_name,month,publishes FROM analytics_rollup_compaction "
                    "WHERE publishes>=16 ORDER BY publishes DESC,table_name,month LIMIT 1"
                )
            ).fetchone()
        if row is None:
            return cleaned
        domain = COMPACTION_DOMAINS[row["table_name"]]
        target = "analytics_" + row["table_name"]
        staging = "analytics_compact_" + row["table_name"] + "_" + uuid.uuid4().hex
        bucket = "toDate(m.bucket)" if domain in {"sources", "topics"} else "m.bucket"
        field = "bucket_day" if domain in {"sources", "topics"} else "bucket_hour"
        params = {"month": row["month"], "domain": domain}

        async def owned_command(statement):
            # A successfully renewed lease outlives both the client total deadline
            # and server execution cap. On connection loss an old statement ends
            # before another owner becomes eligible to replace the partition.
            if await renew(connection) is None:
                raise RuntimeError("Analytics compactor lease was superseded")
            async with connection.transaction():
                await fence(connection)
            await self.client.execute(statement, params)

        try:
            await owned_command(f"CREATE TABLE {staging} AS {target}")
            await owned_command(
                f"INSERT INTO {staging} SELECT r.* FROM {target} r INNER JOIN "
                "(SELECT bucket,generation FROM analytics_bucket_manifest FINAL "
                "WHERE domain={domain:String} AND toYYYYMM(bucket)={month:UInt32}) m "
                f"ON r.{field}={bucket} AND r.generation=m.generation "
                f"WHERE toYYYYMM(r.{field})={{month:UInt32}}"
            )
            await owned_command(
                f"ALTER TABLE {target} REPLACE PARTITION {{month:UInt32}} FROM {staging}"
            )
        finally:
            await self.client.execute(f"DROP TABLE IF EXISTS {staging}")
        async with connection.transaction():
            await fence(connection)
            await connection.execute(
                "UPDATE analytics_rollup_compaction SET publishes=publishes-%s "
                "WHERE table_name=%s AND month=%s",
                (row["publishes"], row["table_name"], row["month"]),
            )
        return True
