"""Direct ClickHouse observations used by overview charts.

Application producers provide stable identities and a monotonic revision for
mutable state. Events are inserted in bounded batches; all calculations happen
at read time over the latest row, with no PostgreSQL analytics tables or
secondary rollup worker.
"""

import uuid
from datetime import UTC, datetime

MAX_BATCH_SIZE = 1000
_ZERO = str(uuid.UUID(int=0))
_NAMESPACE = uuid.UUID("f578f44d-3ce0-4b5e-a903-879be66906ad")

OVERVIEW_EVENTS_DDL = """CREATE TABLE IF NOT EXISTS analytics_overview_events (
    domain LowCardinality(String), id UUID, revision UInt64, deleted UInt8,
    occurred_at Nullable(DateTime64(6, 'UTC')), created_at Nullable(DateTime64(6, 'UTC')),
    task LowCardinality(String), status LowCardinality(String), outcome LowCardinality(String),
    attempt UInt32, duration_ms Nullable(UInt64), automated UInt8,
    source_ids Array(UUID), topic_ids Array(UUID), interest_count UInt32,
    has_generation UInt8, invalidated UInt8, has_recommendations UInt8,
    expires_at Nullable(DateTime64(6, 'UTC')), next_refresh_at Nullable(DateTime64(6, 'UTC'))
) ENGINE = ReplacingMergeTree(revision) ORDER BY (domain, id)"""

_BUCKETS = {
    "hour": "toStartOfHour(occurred_at)",
    "day": "toStartOfDay(occurred_at)",
    "week": "toStartOfInterval(occurred_at, INTERVAL 1 WEEK, 'UTC')",
    "month": "toDateTime(toStartOfMonth(occurred_at), 'UTC')",
}
_JOB_KINDS = {
    "ingestion",
    "article_enrichment",
    "article_image",
    "source_enrichment",
    "analysis",
    "topic_analysis",
    "research_verification",
    "notification",
    "topic-decision",
}


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid overview event identity") from error


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid overview event time") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Overview event times must include a timezone")
    return value.astimezone(UTC)


def _uint(value, *, maximum=2**64 - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid overview event number")
    return value


def _flag(value):
    if type(value) is not bool:
        raise ValueError("Invalid overview event flag")
    return int(value)


def _ids(values):
    if not isinstance(values, (list, tuple)) or len(values) > 1000:
        raise ValueError("Invalid overview dimensions")
    return sorted({_uuid(value) for value in values})


def _identity(domain, *values):
    return str(uuid.uuid5(_NAMESPACE, ":".join((domain, *(str(value) for value in values)))))


def _base(domain, identity, revision, *, deleted=False):
    return {
        "domain": domain,
        "id": _uuid(identity),
        "revision": _uint(revision),
        "deleted": _flag(deleted),
        "occurred_at": None,
        "created_at": None,
        "task": "",
        "status": "",
        "outcome": "",
        "attempt": 0,
        "duration_ms": None,
        "automated": 0,
        "source_ids": [],
        "topic_ids": [],
        "interest_count": 0,
        "has_generation": 0,
        "invalidated": 0,
        "has_recommendations": 0,
        "expires_at": None,
        "next_refresh_at": None,
    }


def _text(value, name, *, max_length=100):
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"Invalid overview {name}")
    return value


class DirectOverviewAnalytics:
    """Store source observations and query charts directly in ClickHouse."""

    def __init__(self, client):
        self.client = client

    async def initialize(self):
        await self.client.execute(OVERVIEW_EVENTS_DDL)

    async def _write(self, rows):
        if len(rows) > MAX_BATCH_SIZE:
            raise ValueError("Overview analytics batch is too large")
        if rows:
            await self.client.insert("analytics_overview_events", rows)

    async def publications(self, records):
        """Write complete publication snapshots, including updated associations.

        When an association is reparented the caller supplies fresh snapshots
        for both its previous and its new article, in the same batch.
        """
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH_SIZE:
            raise ValueError("Invalid publication batch")
        rows = []
        for record in records:
            row = _base(
                "publication",
                record["article_id"],
                record["revision"],
                deleted=record.get("deleted", False),
            )
            if not row["deleted"]:
                row.update(
                    occurred_at=_time(record["published_at"]),
                    created_at=_time(record.get("discovered_at"), optional=True),
                    automated=_flag(record.get("automated", False)),
                    source_ids=_ids(record.get("source_ids", [])),
                    topic_ids=_ids(record.get("topic_ids", [])),
                )
            rows.append(row)
        await self._write(rows)

    async def jobs(self, records):
        """Write job current state plus an immutable fact per completed attempt."""
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH_SIZE // 2:
            raise ValueError("Invalid job batch")
        rows = []
        for record in records:
            job_id = _uuid(record["id"])
            task = _text(record["kind"], "job kind")
            if task not in _JOB_KINDS:
                raise ValueError("Unknown job kind")
            state = _text(record["status"], "job status")
            version = _uint(record["revision"])
            attempt = _uint(record.get("attempt", 0), maximum=2**32 - 1)
            deleted = record.get("deleted", False)
            current = _base("queue", _identity("queue", task, job_id), version, deleted=deleted)
            current.update(
                task=task,
                status=state,
                attempt=attempt,
                created_at=_time(record.get("created_at"), optional=True),
            )
            rows.append(current)
            if not deleted and state in {"succeeded", "failed"}:
                row = _base("job", _identity("attempt", task, job_id, attempt), version)
                duration = record.get("duration_ms")
                row.update(
                    task=task,
                    status=state,
                    attempt=attempt,
                    occurred_at=_time(record["finished_at"]),
                    outcome=_text(record.get("outcome") or "unknown", "job outcome"),
                    duration_ms=_uint(duration) if duration is not None else None,
                )
                rows.append(row)
        await self._write(rows)

    async def decisions(self, records):
        """Record reviewed topic decisions as immutable throughput observations."""
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH_SIZE:
            raise ValueError("Invalid topic decision batch")
        rows = []
        for record in records:
            identity = _identity("topic-decision", _uuid(record["id"]))
            row = _base("job", identity, record["revision"], deleted=record.get("deleted", False))
            if not row["deleted"]:
                if record["status"] not in {"approved", "rejected"}:
                    raise ValueError("Invalid completed topic decision")
                row.update(
                    occurred_at=_time(record["reviewed_at"]),
                    task="topic-decision",
                    status="succeeded",
                    outcome=record["status"],
                )
            rows.append(row)
        await self._write(rows)

    async def feeds(self, records):
        """Write current feed inputs; deadlines are evaluated during each query."""
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH_SIZE:
            raise ValueError("Invalid feed batch")
        rows = []
        for record in records:
            row = _base(
                "feed", record["user_id"], record["revision"], deleted=record.get("deleted", False)
            )
            if not row["deleted"]:
                row.update(
                    interest_count=_uint(record.get("interest_count", 0), maximum=2**32 - 1),
                    has_generation=_flag(record.get("has_generation", False)),
                    invalidated=_flag(record.get("invalidated", False)),
                    has_recommendations=_flag(record.get("has_recommendations", False)),
                    expires_at=_time(record.get("expires_at"), optional=True),
                    next_refresh_at=_time(record.get("next_refresh_at"), optional=True),
                )
            rows.append(row)
        await self._write(rows)

    async def publication_report(self, start, end, *, granularity="day"):
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid publication granularity")
        return await self.client.query(
            f"SELECT {bucket} AS bucket,count() AS published,sum(automated) AS automated,"
            "countIf(created_at IS NOT NULL AND occurred_at>=created_at) AS durations,"
            "quantilesTDigestIf(0.5,0.95)(dateDiff('millisecond',created_at,occurred_at)/1000.,"
            "created_at IS NOT NULL AND occurred_at>=created_at) AS percentiles "
            "FROM (SELECT * FROM analytics_overview_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY bucket ORDER BY bucket "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def publication_overview(self, start, end, *, granularity="day"):
        """Return bucket and overall distribution in one query."""
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid publication granularity")
        return await self.client.query(
            f"SELECT {bucket} AS chart_bucket,grouping(chart_bucket) AS total,"
            "count() AS published,sum(automated) AS automated,"
            "quantilesTDigestIf(0.5,0.95)(dateDiff('millisecond',created_at,occurred_at)/1000.,"
            "created_at IS NOT NULL AND occurred_at>=created_at) AS percentiles "
            "FROM (SELECT * FROM analytics_overview_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY chart_bucket WITH ROLLUP "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def publication_dimensions(self, start, end, *, dimension):
        if dimension not in {"source", "topic"}:
            raise ValueError("Invalid publication dimension")
        column = "source_ids" if dimension == "source" else "topic_ids"
        return await self.client.query(
            "SELECT toString(target) AS id,count() AS publications "
            "FROM (SELECT * FROM analytics_overview_events FINAL WHERE domain='publication') "
            f"ARRAY JOIN {column} AS target "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY target ORDER BY target "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def throughput(self, start, end, *, granularity="day"):
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid throughput granularity")
        return await self.client.query(
            f"SELECT {bucket} AS bucket,countIf(domain='publication') AS articles_published,"
            "countIf(domain='job' AND task='analysis' AND status='succeeded' "
            "AND outcome='applied') AS article_analyses,"
            "countIf(domain='job' AND task='topic-decision' AND status='succeeded') "
            "AS topic_decisions FROM (SELECT * FROM analytics_overview_events FINAL "
            "WHERE domain IN ('publication','job')) WHERE deleted=0 "
            "AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY bucket ORDER BY bucket "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def job_outcomes(self, start, end):
        return await self.client.query(
            "SELECT task AS kind,countIf(status='succeeded') AS completed,"
            "countIf(status='failed') AS failed,countIf(duration_ms IS NOT NULL) AS durations,"
            "quantilesTDigestIf(0.5,0.95)(duration_ms/1000.,duration_ms IS NOT NULL) "
            "AS percentiles "
            "FROM (SELECT * FROM analytics_overview_events FINAL WHERE domain='job') "
            "WHERE deleted=0 AND task!='topic-decision' "
            "AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} "
            "GROUP BY task ORDER BY task "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def queues(self):
        return await self.client.query(
            "SELECT task AS kind,countIf(status='queued') AS queued,"
            "countIf(status='running') AS running,"
            "minOrNullIf(created_at,status='queued') AS oldest_queued_at "
            "FROM (SELECT * FROM analytics_overview_events FINAL WHERE domain='queue') "
            "WHERE deleted=0 AND status IN ('queued','running') GROUP BY task ORDER BY task "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0"
        )

    async def feed_health(self, as_of):
        return await self.client.query(
            "SELECT countIf(state='ready') AS ready,countIf(state='refreshing') AS refreshing,"
            "countIf(state='expired') AS expired,countIf(state='pending') AS pending,"
            "countIf(state='not_needed') AS not_needed,"
            "countIf(interest_count>0 AND next_refresh_at<={now:DateTime64(6,'UTC')}) AS overdue,"
            "countIf(interest_count>0 AND state='ready' AND has_recommendations=0) "
            "AS empty_with_interests "
            "FROM (SELECT *,multiIf(interest_count=0,'not_needed',has_generation=0,'pending',"
            "invalidated=1,'refreshing',isNull(expires_at) OR "
            "expires_at<={now:DateTime64(6,'UTC')},"
            "'expired','ready') AS state FROM (SELECT * FROM analytics_overview_events FINAL "
            "WHERE domain='feed') WHERE deleted=0) "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"now": _time(as_of)},
        )
