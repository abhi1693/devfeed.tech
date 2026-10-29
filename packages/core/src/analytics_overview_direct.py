"""Direct ClickHouse observations used by overview charts.

Application producers provide stable identities and a monotonic revision for
mutable state. Events are inserted in bounded batches; all calculations happen
at read time over the latest row, with no PostgreSQL analytics tables or
secondary rollup worker.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, overload

from devfeed_core.analytics import AnalyticsClient, AnalyticsQueries, SqlRecord
from devfeed_core.analytics_schema import JOB_KINDS as _SOURCE_JOB_KINDS

MAX_BATCH_SIZE = 1000
_ZERO = str(uuid.UUID(int=0))
_NAMESPACE = uuid.UUID("f578f44d-3ce0-4b5e-a903-879be66906ad")


_BUCKETS = {
    "hour": "toStartOfHour(occurred_at)",
    "day": "toStartOfDay(occurred_at)",
    "week": "toStartOfInterval(occurred_at, INTERVAL 1 WEEK, 'UTC')",
    "month": "toDateTime(toStartOfMonth(occurred_at), 'UTC')",
}
_JOB_KINDS = {*_SOURCE_JOB_KINDS.values(), "topic-decision"}


def _uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid overview event identity") from error


@overload
def _time(value: object, *, optional: Literal[False] = False) -> datetime: ...


@overload
def _time(value: object, *, optional: bool) -> datetime | None: ...


def _time(value: object, *, optional: bool = False) -> datetime | None:
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


def _uint(value: object, *, maximum: int = 2**64 - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid overview event number")
    return value


def _flag(value: object) -> int:
    if type(value) is not bool:
        raise ValueError("Invalid overview event flag")
    return int(value)


def _ids(values: Sequence[object]) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > 1000:
        raise ValueError("Invalid overview dimensions")
    return sorted({_uuid(value) for value in values})


def _identity(domain: str, *values: object) -> str:
    return str(uuid.uuid5(_NAMESPACE, ":".join((domain, *(str(value) for value in values)))))


def _base(domain: str, identity: object, revision: int, *, deleted: bool = False) -> SqlRecord:
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
        "name": "",
        "target_id": _ZERO,
        "consecutive_failures": 0,
    }


def _text(value: object, name: str, *, max_length: int = 100) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"Invalid overview {name}")
    return value


def _cohort_range(
    start: datetime | str | None, end: datetime | str | None, column: str
) -> tuple[str, SqlRecord]:
    if (start is None) != (end is None):
        raise ValueError("Both overview cohort bounds are required")
    if start is None:
        return "", {}
    lower, upper = _time(start), _time(end)
    if lower >= upper:
        raise ValueError("Invalid overview cohort range")
    return (
        f" AND {column}>={{start:DateTime64(6,'UTC')}} AND {column}<{{end:DateTime64(6,'UTC')}}",
        {"start": lower, "end": upper},
    )


class DirectOverviewReader:
    """Read chart projections through a scoped query capability."""

    def __init__(self, client: AnalyticsQueries) -> None:
        self.client = client

    async def publication_days(self, start: datetime | str, end: datetime | str) -> list[SqlRecord]:
        return await self.client.query(
            "/* overview:publication-days */ SELECT toDate(occurred_at) AS day,\n"
            "count() AS published "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY day ORDER BY day "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def audience(
        self,
        start: datetime | str,
        end: datetime | str,
        *,
        granularity: str = "day",
        daily: bool = False,
    ) -> list[SqlRecord]:
        bucket = "toDate({column})" if daily else _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid audience granularity")
        if not daily:
            bucket = bucket.replace("occurred_at", "{column}")
        alias, marker = ("day", "audience-days") if daily else ("chart_bucket", "audience")
        open_bucket, account_bucket = (
            bucket.format(column="bucket_hour"),
            bucket.format(column="occurred_at"),
        )
        return await self.client.query(
            f"/* overview:{marker} */ WITH opens AS (SELECT {open_bucket} AS point,"
            "uniqExactMerge(open_state) AS opens,uniqCombined64Merge(reader_state) AS readers "
            "FROM analytics_open_audience_hourly WHERE bucket_hour>={start:DateTime64(6,'UTC')} "
            "AND bucket_hour<{end:DateTime64(6,'UTC')} GROUP BY point),"
            f"accounts AS (SELECT {account_bucket} AS point,count() AS accounts "
            "FROM (SELECT * FROM analytics_user_events FINAL WHERE domain='account') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY point) "
            f"SELECT coalesce(o.point,a.point) AS {alias},coalesce(o.opens,0) AS opens,"
            "coalesce(o.readers,0) AS estimated_readers,coalesce(a.accounts,0) AS new_accounts "
            f"FROM opens o FULL OUTER JOIN accounts a ON o.point=a.point ORDER BY {alias} "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def source_coverage(self, start: datetime | str, end: datetime | str) -> list[SqlRecord]:
        return await self.client.query(
            "/* overview:sources */ SELECT toString(p.source_id) AS id,"
            "coalesce(nullIf(s.name,''),'Unknown source') AS name,p.publications,"
            "coalesce(s.consecutive_failures,0) AS consecutive_failures,"
            "sum(p.publications) OVER() AS total FROM ("
            "SELECT arrayJoin(source_ids) AS source_id,count() AS publications "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY source_id) p "
            "LEFT JOIN (SELECT id,name,consecutive_failures FROM analytics_pipeline_events FINAL "
            "WHERE domain='source' AND deleted=0) s ON s.id=p.source_id "
            "ORDER BY p.publications DESC,name,id LIMIT 10 "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def topic_coverage(self, start: datetime | str, end: datetime | str) -> list[SqlRecord]:
        return await self.client.query(
            "/* overview:topics */ WITH memberships AS ("
            "SELECT target_id AS topic_id,countIf(task='follow') AS followers,"
            "countIf(task='inferred') AS inferred_users FROM ("
            "SELECT m.target_id,m.task,if(m.task='follow',m.occurred_at,f.occurred_at) "
            "AS cohort_at FROM (SELECT * FROM analytics_user_events FINAL "
            "WHERE domain='membership') m LEFT JOIN ("
            "SELECT id,occurred_at FROM analytics_user_events FINAL "
            "WHERE domain='feed' AND deleted=0"
            ") f ON f.id=m.user_id WHERE m.deleted=0 AND m.task IN ('follow','inferred')) "
            "WHERE cohort_at>={start:DateTime64(6,'UTC')} "
            "AND cohort_at<{end:DateTime64(6,'UTC')} GROUP BY target_id),"
            "publications AS (SELECT arrayJoin(topic_ids) AS topic_id,count() AS publications "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY topic_id),"
            "activity AS (SELECT topic_id,sum(followers) AS followers,"
            "sum(inferred_users) AS inferred_users,sum(publications) AS publications FROM ("
            "SELECT topic_id,followers,inferred_users,0 AS publications FROM memberships "
            "UNION ALL SELECT topic_id,0 AS followers,0 AS inferred_users,publications "
            "FROM publications) GROUP BY topic_id) "
            "SELECT toString(a.topic_id) AS id,coalesce(nullIf(t.name,''),'Unknown topic') AS name,"
            "a.followers,a.inferred_users,a.publications FROM activity a "
            "LEFT JOIN (SELECT id,name FROM analytics_pipeline_events FINAL "
            "WHERE domain='topic' AND deleted=0) t ON t.id=a.topic_id "
            "WHERE a.followers>0 OR a.inferred_users>0 OR a.publications>0 "
            "ORDER BY a.followers DESC,a.inferred_users DESC,name,id LIMIT 10 "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def publication_report(
        self, start: datetime | str, end: datetime | str, *, granularity: str = "day"
    ) -> list[SqlRecord]:
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid publication granularity")
        return await self.client.query(
            f"SELECT {bucket} AS bucket,count() AS published,sum(automated) AS automated,"
            "countIf(created_at IS NOT NULL AND occurred_at>=created_at) AS durations,"
            "quantilesTDigestIf(0.5,0.95)(dateDiff('millisecond',created_at,occurred_at)/1000.,"
            "created_at IS NOT NULL AND occurred_at>=created_at) AS percentiles "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY bucket ORDER BY bucket "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def publication_overview(
        self, start: datetime | str, end: datetime | str, *, granularity: str = "day"
    ) -> list[SqlRecord]:
        """Return bucket and overall distribution in one query."""
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid publication granularity")
        return await self.client.query(
            f"/* overview:publishing */ SELECT {bucket} AS chart_bucket,\n"
            "grouping(chart_bucket) AS total,"
            "count() AS published,sum(automated) AS automated,"
            "quantilesTDigestIf(0.5,0.95)(dateDiff('millisecond',created_at,occurred_at)/1000.,"
            "created_at IS NOT NULL AND occurred_at>=created_at) AS percentiles "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication') "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY chart_bucket WITH ROLLUP "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def publication_dimensions(
        self, start: datetime | str, end: datetime | str, *, dimension: str
    ) -> list[SqlRecord]:
        if dimension not in {"source", "topic"}:
            raise ValueError("Invalid publication dimension")
        column = "source_ids" if dimension == "source" else "topic_ids"
        return await self.client.query(
            "SELECT toString(target) AS id,count() AS publications "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication') "
            f"ARRAY JOIN {column} AS target "
            "WHERE deleted=0 AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY target ORDER BY target "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def throughput(
        self, start: datetime | str, end: datetime | str, *, granularity: str = "day"
    ) -> list[SqlRecord]:
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid throughput granularity")
        return await self.client.query(
            f"/* overview:throughput */ SELECT {bucket} AS chart_bucket,\n"
            "countIf(domain='publication') AS articles_published,"
            "countIf(domain='job' AND task='analysis' AND status='succeeded' "
            "AND outcome='applied') AS article_analyses,"
            "countIf(domain='job' AND task='topic-decision' AND status='succeeded') "
            "AS topic_decisions FROM (SELECT * FROM analytics_pipeline_events FINAL "
            "WHERE domain IN ('publication','job')) WHERE deleted=0 "
            "AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} GROUP BY chart_bucket ORDER BY chart_bucket "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def job_outcomes(self, start: datetime | str, end: datetime | str) -> list[SqlRecord]:
        return await self.client.query(
            "/* overview:job-outcomes */ SELECT task AS kind,\n"
            "countIf(status='succeeded') AS completed,"
            "countIf(status='failed') AS failed,countIf(duration_ms IS NOT NULL) AS durations,"
            "quantilesTDigestIf(0.5,0.95)(duration_ms/1000.,duration_ms IS NOT NULL) "
            "AS percentiles "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='job') "
            "WHERE deleted=0 AND task!='topic-decision' "
            "AND occurred_at>={start:DateTime64(6,'UTC')} "
            "AND occurred_at<{end:DateTime64(6,'UTC')} "
            "GROUP BY task ORDER BY task "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def queues(
        self, start: datetime | str | None = None, end: datetime | str | None = None
    ) -> list[SqlRecord]:
        cohort, parameters = _cohort_range(start, end, "created_at")
        return await self.client.query(
            "/* overview:queues */ SELECT task AS kind,countIf(status='queued') AS queued,"
            "countIf(status='running') AS running,"
            "minOrNullIf(created_at,status='queued') AS oldest_queued_at "
            "FROM (SELECT * FROM analytics_pipeline_events FINAL WHERE domain='queue') "
            "WHERE deleted=0 AND status IN ('queued','running')"
            + cohort
            + " GROUP BY task ORDER BY task "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            parameters,
        )

    async def feed_health(
        self,
        as_of: datetime | str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> list[SqlRecord]:
        cohort, parameters = _cohort_range(start, end, "coalesce(occurred_at,created_at)")
        return await self.client.query(
            "/* overview:feed-health */ SELECT countIf(state='ready') AS ready,\n"
            "countIf(state='refreshing') AS refreshing,"
            "countIf(state='expired') AS expired,countIf(state='pending') AS pending,"
            "countIf(state='not_needed') AS not_needed,"
            "countIf(interest_count>0 AND next_refresh_at<={now:DateTime64(6,'UTC')}) AS overdue,"
            "countIf(interest_count>0 AND state='ready' AND has_recommendations=0) "
            "AS empty_with_interests "
            "FROM (SELECT *,multiIf(interest_count=0,'not_needed',has_generation=0,'pending',"
            "invalidated=1,'refreshing',isNull(expires_at) OR "
            "expires_at<={now:DateTime64(6,'UTC')},"
            "'expired','ready') AS state FROM (SELECT * FROM analytics_user_events FINAL "
            "WHERE domain='feed') WHERE deleted=0" + cohort + ") "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"now": _time(as_of), **parameters},
        )


class DirectOverviewAnalytics(DirectOverviewReader):
    client: AnalyticsClient
    """Store source observations and query charts directly in ClickHouse."""

    def __init__(self, client: AnalyticsClient) -> None:
        self.client = client

    async def _write(self, rows: Sequence[SqlRecord]) -> None:
        if len(rows) > MAX_BATCH_SIZE:
            raise ValueError("Overview analytics batch is too large")
        for table, domains in (
            ("analytics_pipeline_events", {"publication", "job", "queue", "source", "topic"}),
            ("analytics_user_events", {"account", "feed", "membership"}),
        ):
            selected = [row for row in rows if row["domain"] in domains]
            if table == "analytics_user_events":
                selected = [row | {"user_id": row.get("user_id")} for row in selected]
            if selected:
                await self.client.insert(table, selected)

    async def publish(self, changes: Sequence[SqlRecord], *, revision: int) -> None:
        """Publish sanitized source snapshots after their business transaction commits.

        Publication and feed snapshots are hydrated by the application publisher,
        because their inputs span associations and operational account state.
        A reparented identity is represented as an old-key deletion plus new-key
        snapshot. Every retry keeps the same revision and snapshot payload.
        """
        groups: dict[str, list[SqlRecord]] = {
            name: []
            for name in (
                "publications",
                "jobs",
                "decisions",
                "feeds",
                "accounts",
                "sources",
                "topics",
                "memberships",
            )
        }
        for change in self._bounded(changes):
            self._group_change(groups, change, _uint(revision))
        for name, records in groups.items():
            size = MAX_BATCH_SIZE // 2 if name == "jobs" else MAX_BATCH_SIZE
            for offset in range(0, len(records), size):
                await getattr(self, name)(records[offset : offset + size])

    @staticmethod
    def _group_change(groups: dict[str, list[SqlRecord]], change: SqlRecord, revision: int) -> None:
        source = change["source_table"]
        record = change.get("record")
        deleted = record is None or change.get("deleted", False) or change.get("operation") == "D"
        record = record or change.get("identity") or change.get("before_record") or {}
        if "publication" in change:
            groups["publications"].append(change["publication"] | {"revision": revision})
        if "feed" in change:
            groups["feeds"].append(change["feed"] | {"revision": revision})
        if source in _SOURCE_JOB_KINDS:
            groups["jobs"].append(
                {
                    "id": record["id"],
                    "kind": _SOURCE_JOB_KINDS[source],
                    "revision": revision,
                    "status": record.get("status") or "unknown",
                    "attempt": record.get("attempts") or 0,
                    "created_at": record.get("created_at"),
                    "finished_at": record.get("finished_at"),
                    "duration_ms": record.get("duration_ms"),
                    "outcome": record.get("outcome"),
                    "deleted": bool(deleted),
                    "attempt_only": change.get("attempt_only", False),
                }
            )
        elif source == "user_accounts":
            groups["accounts"].append(record | {"revision": revision, "deleted": bool(deleted)})
        elif source in {"sources", "topics"}:
            groups[source].append(record | {"revision": revision, "deleted": bool(deleted)})
        elif source in {"user_topics", "user_interests"}:
            groups["memberships"].append(
                {
                    "user_id": record["user_id"],
                    "topic_id": record["topic_id"],
                    "kind": "follow" if source == "user_topics" else "inferred",
                    "occurred_at": record.get(
                        "created_at" if source == "user_topics" else "computed_at"
                    ),
                    "revision": revision,
                    "deleted": bool(
                        deleted
                        or (source == "user_interests" and record.get("reason") != "related_topic")
                    ),
                }
            )
        elif source == "topic_proposals":
            reviewed = record.get("status") in {"approved", "rejected"}
            groups["decisions"].append(
                record | {"revision": revision, "deleted": bool(deleted or not reviewed)}
            )

    async def publications(self, records: Sequence[SqlRecord]) -> None:
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

    async def jobs(self, records: Sequence[SqlRecord]) -> None:
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
            if not record.get("attempt_only", False):
                rows.append(current)
            if (
                not deleted
                and state in {"succeeded", "failed"}
                and record.get("finished_at") is not None
            ):
                row = _base(
                    "job",
                    self._attempt_identity(task, job_id, attempt, record["finished_at"]),
                    version,
                )
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

    @staticmethod
    def _attempt_identity(
        task: str, job_id: uuid.UUID | str, attempt: int, finished_at: datetime | str
    ) -> str:
        # Completion timestamps distinguish unnumbered historical attempts
        # from subsequent completions that also lack an attempt counter.
        suffix = () if attempt else (_time(finished_at).isoformat(),)
        return _identity("attempt", task, job_id, attempt, *suffix)

    async def decisions(self, records: Sequence[SqlRecord]) -> None:
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

    async def feeds(self, records: Sequence[SqlRecord]) -> None:
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
                    occurred_at=_time(record.get("computed_at"), optional=True),
                    created_at=_time(record.get("created_at"), optional=True),
                    interest_count=_uint(record.get("interest_count", 0), maximum=2**32 - 1),
                    has_generation=_flag(record.get("has_generation", False)),
                    invalidated=_flag(record.get("invalidated", False)),
                    has_recommendations=_flag(record.get("has_recommendations", False)),
                    expires_at=_time(record.get("expires_at"), optional=True),
                    next_refresh_at=_time(record.get("next_refresh_at"), optional=True),
                )
            rows.append(row)
        await self._write(rows)

    async def accounts(self, records: Sequence[SqlRecord]) -> None:
        rows = []
        for record in self._bounded(records):
            row = _base(
                "account", record["id"], record["revision"], deleted=record.get("deleted", False)
            )
            if not row["deleted"]:
                row["occurred_at"] = _time(record["created_at"])
            rows.append(row)
        await self._write(rows)

    async def sources(self, records: Sequence[SqlRecord]) -> None:
        await self._dimensions("source", records)

    async def topics(self, records: Sequence[SqlRecord]) -> None:
        await self._dimensions("topic", records)

    async def _dimensions(self, domain: str, records: Sequence[SqlRecord]) -> None:
        rows = []
        for record in self._bounded(records):
            row = _base(
                domain, record["id"], record["revision"], deleted=record.get("deleted", False)
            )
            if not row["deleted"]:
                row["name"] = _text(record.get("name") or "Unnamed", "name", max_length=200)
                row["consecutive_failures"] = _uint(
                    record.get("consecutive_failures") or 0, maximum=2**32 - 1
                )
            rows.append(row)
        await self._write(rows)

    async def memberships(self, records: Sequence[SqlRecord]) -> None:
        rows = []
        for record in self._bounded(records):
            kind = record["kind"]
            if kind not in {"follow", "inferred"}:
                raise ValueError("Invalid analytics membership kind")
            user, topic = _uuid(record["user_id"]), _uuid(record["topic_id"])
            row = _base(
                "membership",
                _identity("membership", kind, user, topic),
                record["revision"],
                deleted=record.get("deleted", False),
            )
            row.update(
                task=kind,
                target_id=topic,
                user_id=user,
                occurred_at=_time(record.get("occurred_at"), optional=True),
            )
            rows.append(row)
        await self._write(rows)

    @staticmethod
    def _bounded(records: Sequence[SqlRecord]) -> Sequence[SqlRecord]:
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH_SIZE:
            raise ValueError("Invalid overview batch")
        return records
