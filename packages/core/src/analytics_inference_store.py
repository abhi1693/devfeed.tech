"""Direct asynchronous ClickHouse storage for content-free inference observations.

Each invocation uses the same ID and start time for its running and completed
observation. ClickHouse keeps the completed revision when a delayed start is
retried. No prompts, hashes, responses, or credentials are stored.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta

MAX_BATCH_SIZE = 1000
INFERENCE_EVENTS_DDL = """CREATE TABLE IF NOT EXISTS analytics_inference_events (
    id UUID, started_at DateTime64(6, 'UTC'), revision UInt8,
    task LowCardinality(String), model LowCardinality(String), effort LowCardinality(String),
    model_is_null UInt8, model_is_empty UInt8, effort_is_null UInt8, effort_is_empty UInt8,
    reason Nullable(String),
    status LowCardinality(String), input_tokens UInt64, cached_input_tokens UInt64,
    reported_cached_input_tokens UInt64, cache_write_input_tokens UInt64, has_input_tokens UInt8,
    output_tokens UInt64, reasoning_tokens UInt64, web_searches UInt64,
    duration_ms UInt64, has_duration UInt8
) ENGINE = ReplacingMergeTree(revision)
PARTITION BY toYYYYMM(started_at) ORDER BY (started_at, id)"""

_BUCKETS = {
    "hour": "toStartOfHour(started_at)",
    "day": "toStartOfDay(started_at)",
    "week": "toStartOfInterval(started_at, INTERVAL 1 WEEK, 'UTC')",
    "month": "toDateTime(toStartOfMonth(started_at), 'UTC')",
}


def _timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid inference start time") from error
    if not isinstance(value, datetime):
        raise ValueError("Invalid inference start time")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _number(value):
    raw = str(value)
    return int(raw) if re.fullmatch(r"[0-9]{1,12}", raw) else 0


def _row(record):
    if not isinstance(record, dict):
        raise ValueError("Inference observation must be an object")
    try:
        identity = str(uuid.UUID(str(record["id"])))
        started = _timestamp(record["started_at"])
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("Inference observation needs a valid identity and start time") from error
    status = record.get("status") or "unknown"
    if not isinstance(status, str):
        raise ValueError("Invalid inference status")
    task, model, effort = (
        record.get("operation") or "unknown",
        record.get("model") or "Unknown model",
        record.get("reasoning_effort") or "Inherited / unknown",
    )
    if any(
        not isinstance(value, str) or len(value) > 200 for value in (status, task, model, effort)
    ):
        raise ValueError("Invalid inference dimensions")
    tokens = record.get("tokens") or {}
    if not isinstance(tokens, dict):
        raise ValueError("Invalid inference token counters")
    input_tokens, output_tokens = (
        _number(tokens.get("inputTokens")),
        _number(tokens.get("outputTokens")),
    )
    try:
        searches = max(int(record.get("web_searches") or 0), 0)
        duration = int(record["duration_ms"]) if record.get("duration_ms") is not None else None
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Invalid inference counters") from error
    if searches >= 2**64 or (duration is not None and duration >= 2**64):
        raise ValueError("Inference counter exceeds storage range")
    return {
        "id": identity,
        "started_at": started,
        "revision": int(status != "running"),
        "task": task,
        "model": model,
        "effort": effort,
        "model_is_null": int(record.get("model") is None),
        "model_is_empty": int(record.get("model") == ""),
        "effort_is_null": int(record.get("reasoning_effort") is None),
        "effort_is_empty": int(record.get("reasoning_effort") == ""),
        "reason": record.get("reason")
        if isinstance(record.get("reason"), str)
        and record.get("reason") in {"source_review", "verify_research"}
        else None,
        "status": status,
        "input_tokens": input_tokens,
        "cached_input_tokens": min(input_tokens, _number(tokens.get("cachedInputTokens"))),
        "reported_cached_input_tokens": _number(tokens.get("cachedInputTokens")),
        "cache_write_input_tokens": _number(tokens.get("cacheWriteInputTokens")),
        "has_input_tokens": int("inputTokens" in tokens),
        "output_tokens": output_tokens,
        "reasoning_tokens": min(output_tokens, _number(tokens.get("reasoningOutputTokens"))),
        "web_searches": searches,
        "duration_ms": max(duration or 0, 0),
        "has_duration": int(duration is not None and duration >= 0),
    }


class InferenceStore:
    """Insert and query inference events directly without PostgreSQL reporting tables."""

    def __init__(self, client):
        self.client = client

    async def initialize(self):
        await self.client.execute(INFERENCE_EVENTS_DDL)

    async def write(self, records):
        """Write one bounded batch; the caller retries with the same IDs and start times.

        A successful insert is acknowledged only after ClickHouse persists its
        asynchronous insert. An unavailable ClickHouse raises to the caller.
        """
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH_SIZE:
            raise ValueError("Invalid inference batch size")
        rows = [_row(record) for record in records]
        if rows:
            await self.client.insert("analytics_inference_events", rows)
        return len(rows)

    async def relationship_count(self, since):
        """Count recent calls; admission must serialize separately across workers."""
        rows = await self.client.query(
            "SELECT count() AS calls FROM analytics_inference_events FINAL "
            "WHERE started_at >= {since:DateTime64(6, 'UTC')} "
            "AND task IN ('relationship_research', 'relationship_verification') "
            "SETTINGS optimize_move_to_prewhere_if_final=0",
            {"since": _timestamp(since)},
        )
        if len(rows) != 1 or type(rows[0].get("calls")) is not int:
            raise RuntimeError("Invalid inference count")
        return rows[0]["calls"]

    async def exists(self, identity, started_at):
        """Check an exact invocation before accepting a retried reservation."""
        try:
            identity = str(uuid.UUID(str(identity)))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid inference identity") from error
        rows = await self.client.query(
            "SELECT count() AS calls FROM analytics_inference_events FINAL "
            "WHERE started_at={started:DateTime64(6, 'UTC')} AND id={id:UUID}",
            {"started": _timestamp(started_at), "id": identity},
        )
        if len(rows) != 1 or type(rows[0].get("calls")) is not int:
            raise RuntimeError("Invalid inference identity count")
        return bool(rows[0]["calls"])

    async def aggregate(self, start, end, *, granularity="day"):
        """Return bucket, total and dimension aggregates in one consistent query."""
        if granularity not in _BUCKETS:
            raise ValueError("Invalid inference granularity")
        start, end = _timestamp(start), _timestamp(end)
        if end <= start:
            raise ValueError("Invalid inference time range")
        query = f"""/* overview:inference */ WITH grouped AS (
            SELECT {_BUCKETS[granularity]} AS chart_bucket,
              grouping(chart_bucket) AS gb,grouping(task) AS gt,
              grouping(model) AS gm,grouping(effort) AS ge,task,model,effort,
              count() AS calls,countIf(status='failed') AS failed,
              countIf(input_tokens+output_tokens=0) AS unreported,
              sum(toUInt128(input_tokens)) AS input_tokens,
              sum(toUInt128(cached_input_tokens)) AS cached_input_tokens,
              sum(toUInt128(output_tokens)) AS output_tokens,
              sum(toUInt128(reasoning_tokens)) AS reasoning_tokens,
              sum(toUInt128(web_searches)) AS web_searches,
              quantilesTDigestIf(0.5,0.95)(duration_ms,has_duration=1) AS percentiles
            FROM analytics_inference_events FINAL
            WHERE started_at >= {{start:DateTime64(6,'UTC')}}
              AND started_at < {{end:DateTime64(6,'UTC')}}
            GROUP BY GROUPING SETS ((chart_bucket),(task),(model),(effort),())
          ), classified AS (
            SELECT *,multiIf(gb=0,'activity',gt=0,'tasks',gm=0,'models',
              ge=0,'efforts','summary') AS dimension,
              multiIf(gt=0,task,gm=0,model,ge=0,effort,'') AS label FROM grouped
          ), ranked AS (
            SELECT *,row_number() OVER(PARTITION BY dimension
              ORDER BY input_tokens+output_tokens DESC,label) AS rank FROM classified
          ) SELECT dimension,if(dimension IN ('tasks','models','efforts')
              AND rank>8,'Other',label) AS name,chart_bucket,
            sum(calls) AS calls,sum(failed) AS failed,sum(unreported) AS unreported,
            sum(input_tokens) AS input_tokens,sum(cached_input_tokens) AS cached_input_tokens,
            sum(output_tokens) AS output_tokens,sum(reasoning_tokens) AS reasoning_tokens,
            sum(web_searches) AS web_searches,any(percentiles) AS percentiles
          FROM ranked GROUP BY dimension,name,chart_bucket
          ORDER BY dimension,name='Other',sum(input_tokens)+sum(output_tokens) DESC,name
          SETTINGS optimize_move_to_prewhere_if_final=0,prefer_column_name_to_alias=1"""
        return await self.client.query(query, {"start": start, "end": end})


class RelationshipQuota:
    """Serialize a relationship-call reservation without PG analytics tables.

    The connection factory supplies an async context manager for a PostgreSQL
    connection; a transaction-scoped advisory lock fences the count and the
    confirmed ClickHouse insert across workers. Return success only after that
    transaction commits. A crash after insertion may reserve a call which did
    not execute, but cannot authorize an uncounted call.
    """

    def __init__(self, store, connection_factory, *, timeout_seconds=30):
        if not callable(connection_factory) or not 0 < timeout_seconds <= 300:
            raise ValueError("Invalid relationship quota configuration")
        self.store = store
        self.connection_factory = connection_factory
        self.timeout_seconds = timeout_seconds

    async def admit(self, record, limit):
        if type(limit) is not int or limit < 0:
            raise ValueError("Invalid relationship budget")
        row = _row(record)
        if row["status"] != "running" or row["task"] not in {
            "relationship_research",
            "relationship_verification",
        }:
            raise ValueError("Invalid relationship reservation")
        async with asyncio.timeout(self.timeout_seconds):
            async with self.connection_factory() as connection, connection.transaction():
                await connection.execute(
                    "SELECT set_config('statement_timeout',%s,true)",
                    (f"{self.timeout_seconds}s",),
                )
                await connection.execute("SELECT pg_advisory_xact_lock(%s,%s)", (0x444652, 0))
                if (
                    await self.store.relationship_count(datetime.now(UTC) - timedelta(days=1))
                    >= limit
                ):
                    return False
                if await self.store.exists(row["id"], row["started_at"]):
                    return False
                await self.store.write([record])
        return True
