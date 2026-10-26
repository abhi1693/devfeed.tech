"""Typed domain facts and bounded, replay-safe ClickHouse projections.

Raw observations retain analytical detail. Idempotent argMax states select one
current fact per identity/hour; chart reads use separately published bucket
summaries, never a relational mirror or an all-history FINAL calculation.
"""

from datetime import UTC, datetime

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
QUANTILES = "AggregateFunction(quantilesTDigest(0.5, 0.95), Float64)"
READERS = "AggregateFunction(uniqCombined64, String)"
DIMENSIONS = (
    ("task", "LowCardinality(String)"),
    ("model", "LowCardinality(String)"),
    ("effort", "LowCardinality(String)"),
    ("status", "LowCardinality(String)"),
    ("outcome", "LowCardinality(String)"),
    ("content_type", "LowCardinality(String)"),
    ("source_ids", "Array(UUID)"),
    ("topic_ids", "Array(UUID)"),
    ("viewer_key", "String"),
)
COUNTERS = (
    "published",
    "automated",
    "opens",
    "accounts_created",
    "calls",
    "unreported",
    "web_searches",
    "completed",
    "failed",
    "useful",
    "topic_decisions",
)
TOKENS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "job_tokens")
PAYLOAD = (
    ("deleted", "UInt8"),
    ("entity_id", "UUID"),
    ("parent_id", "UUID"),
    ("attempt", "UInt32"),
    ("event_time", "Nullable(DateTime64(6, 'UTC'))"),
    ("ordinal", "UInt64"),
    ("stage", "LowCardinality(String)"),
    ("stage_call", "UInt64"),
    ("escalated", "UInt8"),
    ("decision_tokens", "Int128"),
    ("charged_tokens", "Nullable(Int128)"),
    ("has_job_tokens", "UInt8"),
    ("origin_created_at", "Nullable(DateTime64(6, 'UTC'))"),
    ("ended_at", "Nullable(DateTime64(6, 'UTC'))"),
    *DIMENSIONS,
    *((name, "UInt64") for name in COUNTERS),
    *((name, "Int128") for name in TOKENS),
    ("duration_ms", "UInt64"),
    ("has_duration", "UInt8"),
    ("delay_seconds", "Float64"),
    ("has_delay", "UInt8"),
)
PAYLOAD_NAMES = tuple(name for name, _ in PAYLOAD)
STATE_TYPE = (
    "AggregateFunction(argMax, Tuple("
    + ", ".join(kind for _, kind in PAYLOAD)
    + "), Tuple(UInt64, UInt64))"
)


def _columns(columns):
    return ", ".join(f"`{name}` {kind}" for name, kind in columns)


def _rollup(name, dimensions, values, *, daily=False):
    bucket = "bucket_day" if daily else "bucket_hour"
    bucket_type = "Date" if daily else "DateTime('UTC')"
    fields = ((bucket, bucket_type), ("generation", "UInt64"), *dimensions, *values)
    ordering = ", ".join((bucket, "generation", *(name for name, _ in dimensions)))
    return (
        f"CREATE TABLE IF NOT EXISTS analytics_{name} ({_columns(fields)}) "
        f"ENGINE = MergeTree PARTITION BY toYYYYMM({bucket}) ORDER BY ({ordering})"
    )


ROLLUP_DDL = (
    _rollup(
        "publishing_hourly",
        (),
        (
            ("published", "UInt64"),
            ("automated", "UInt64"),
            ("delay_seconds_state", QUANTILES),
        ),
    ),
    _rollup(
        "audience_hourly",
        (),
        (
            ("opens", "UInt64"),
            ("accounts_created", "UInt64"),
            ("readers_state", READERS),
        ),
    ),
    _rollup(
        "processing_hourly",
        (
            ("kind", "LowCardinality(String)"),
            ("status", "LowCardinality(String)"),
            ("outcome", "LowCardinality(String)"),
        ),
        (
            ("completed", "UInt64"),
            ("failed", "UInt64"),
            ("useful", "UInt64"),
            ("duration_seconds_state", QUANTILES),
        ),
    ),
    _rollup(
        "inference_hourly",
        tuple((name, "LowCardinality(String)") for name in ("task", "model", "effort", "status")),
        (
            *((name, "UInt64") for name in ("calls", "failed", "unreported", "web_searches")),
            *((name, "Int128") for name in TOKENS if name != "job_tokens"),
            ("duration_ms_state", QUANTILES),
        ),
    ),
    _rollup("source_daily", (("source_id", "UUID"),), (("published", "UInt64"),), daily=True),
    _rollup("topic_daily", (("topic_id", "UUID"),), (("published", "UInt64"),), daily=True),
)


def initialization_sql():
    raw_columns = (
        ("domain", "LowCardinality(String)"),
        ("event_id", "UInt64"),
        ("side", "UInt8"),
        ("captured_at", "DateTime64(6, 'UTC')"),
        ("bucket_hour", "DateTime('UTC')"),
        ("fact_id", "UUID"),
        ("generation", "UInt64"),
        *PAYLOAD,
    )
    yield (
        f"CREATE TABLE IF NOT EXISTS analytics_fact_events ({_columns(raw_columns)}) "
        "ENGINE = ReplacingMergeTree(generation) PARTITION BY toYYYYMM(captured_at) "
        "ORDER BY (domain, captured_at, event_id, fact_id, bucket_hour, side)"
    )
    yield (
        "CREATE TABLE IF NOT EXISTS analytics_fact_state (domain LowCardinality(String), "
        "bucket_hour DateTime('UTC'), fact_id UUID, state " + STATE_TYPE + ") "
        "ENGINE = AggregatingMergeTree PARTITION BY toYYYYMM(bucket_hour) "
        "ORDER BY (domain, bucket_hour, fact_id)"
    )
    payload = ", ".join(f"`{name}`" for name in PAYLOAD_NAMES)
    yield (
        "CREATE MATERIALIZED VIEW IF NOT EXISTS analytics_fact_state_mv "
        "TO analytics_fact_state AS SELECT domain, bucket_hour, fact_id, "
        f"argMaxState(tuple({payload}), tuple(generation, event_id)) AS state "
        "FROM analytics_fact_events GROUP BY domain, bucket_hour, fact_id"
    )
    yield (
        "CREATE TABLE IF NOT EXISTS analytics_bucket_manifest (domain LowCardinality(String), "
        "bucket DateTime('UTC'), generation UInt64, updated_at DateTime64(6, 'UTC'), "
        "lag_seconds Float64) ENGINE = ReplacingMergeTree(generation) ORDER BY (domain, bucket)"
    )
    yield (
        "CREATE TABLE IF NOT EXISTS analytics_audience_archive_daily (day Date, "
        "opens Nullable(UInt64), readers Nullable(Float64), accounts_created Nullable(UInt64), "
        "published Nullable(UInt64), version UInt64) ENGINE = ReplacingMergeTree(version) "
        "ORDER BY day"
    )
    yield (
        "CREATE TABLE IF NOT EXISTS analytics_pipeline_status (version UInt64, ready UInt8, "
        "updated_at DateTime64(6, 'UTC'), capture_started_at DateTime64(6, 'UTC'), "
        "reader_coverage_start Date, lag_seconds Float64) "
        "ENGINE = ReplacingMergeTree(version) ORDER BY tuple()"
    )
    yield from ROLLUP_DDL


TABLE_NAMES = {
    "analytics_fact_events",
    "analytics_bucket_manifest",
    "analytics_audience_archive_daily",
    "analytics_pipeline_status",
}


def empty_fact(domain, identity, at, *, event_id, captured_at, generation, side=1, deleted=False):
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    row = {
        "domain": domain,
        "event_id": event_id,
        "side": side,
        "captured_at": captured_at,
        "bucket_hour": at.replace(minute=0, second=0, microsecond=0),
        "fact_id": str(identity),
        "generation": generation,
        "deleted": int(deleted),
        "entity_id": str(identity),
        "parent_id": ZERO_UUID,
        "event_time": at,
        "origin_created_at": None,
        "ended_at": None,
    }
    for name, kind in PAYLOAD:
        if name not in row:
            row[name] = (
                None
                if kind.startswith("Nullable")
                else []
                if kind.startswith("Array")
                else ""
                if "String" in kind
                else 0
            )
    return row


def dirty_buckets(rows, generation):
    buckets = set()
    for row in rows:
        if row["domain"] == "decisions":
            continue
        hour = row["bucket_hour"]
        if isinstance(hour, str):
            hour = datetime.fromisoformat(hour.replace("Z", "+00:00"))
        buckets.add((row["domain"], hour))
        if row["domain"] == "publishing":
            day = hour.replace(hour=0)
            buckets.update((("sources", day), ("topics", day)))
    return [
        dict(domain=domain, bucket=bucket, generation=generation)
        for domain, bucket in sorted(buckets)
    ]
