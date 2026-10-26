"""Bounded Overview reads over published ClickHouse bucket and gauge generations.

Overview never joins raw event histories. Rollup generations are published through
small manifests after every bucket is complete; retries cannot expose partial sums.
"""

import asyncio
import math
from datetime import UTC, datetime, time, timedelta
from typing import get_args

from devfeed_admin_api.overview_sections import (
    AudienceBucket,
    AudienceSection,
    CoverageSection,
    InferenceBucket,
    InferenceSection,
    InferenceSummary,
    JobOutcome,
    OverviewSectionReport,
    PersonalizationCounts,
    ProcessingSection,
    PublicationBucket,
    PublicationSummary,
    PublishingSection,
    QueuePressure,
    SectionName,
    SourceCoverage,
    ThroughputBucket,
    TopicCoverage,
    UsageBreakdown,
)
from devfeed_admin_api.overview_windows import ReportWindow

ANALYTICAL_GROUPS = frozenset(get_args(SectionName)) - {"operations"}
START = "{start:DateTime64(6, 'UTC')}"
END = "{end:DateTime64(6, 'UTC')}"
OBSERVED_END = "least(" + END + ", {observed_end:DateTime64(6, 'UTC')})"
QUANTILES = "quantilesTDigestMerge(0.5, 0.95)"
COUNTERS = (
    "calls",
    "failed",
    "unreported",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "web_searches",
)


async def _gather(*coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _number(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def _percentiles(row):
    values = row.get("percentiles") or [None, None]
    return _number(values[0]), _number(values[1])


class Reports:
    def __init__(self, client, window: ReportWindow, now, *, capture_started_at):
        if window.start is None or window.end is None:
            raise ValueError("Analytical reports require explicit calendar dates")
        self.client = client
        self.window = window
        self.days = window.days
        self.now = now.astimezone(UTC)
        self.start = datetime.combine(window.start, time.min, tzinfo=UTC)
        self.end = datetime.combine(window.end, time.min, tzinfo=UTC)
        capture_started_at = capture_started_at.astimezone(UTC)
        self.capture_day = capture_started_at.date()
        self.reader_start = (capture_started_at - timedelta(days=29)).date()
        self.params = {
            "start": self.start,
            "end": self.end,
            "observed_end": self.now.replace(minute=0, second=0, microsecond=0)
            + timedelta(hours=1),
            "capture_day": self.capture_day,
            "reader_start": self.reader_start,
        }
        self.hourly = self.days <= 7 and window.start >= self.capture_day
        self.monthly = self.days > 366
        self.weekly = 90 < self.days <= 366
        self.granularity = (
            "hour" if self.hourly else "month" if self.monthly else "week" if self.weekly else "day"
        )
        self.bucket = self.bucket_sql("r.bucket_hour")
        self.slots = asyncio.Semaphore(2)

    def bucket_sql(self, column):
        if self.hourly:
            return f"toStartOfHour({column})"
        if self.monthly:
            return f"toDateTime(toStartOfMonth(toTimeZone({column}, 'UTC')), 'UTC')"
        if self.weekly:
            return f"toStartOfInterval({column}, INTERVAL 1 WEEK, 'UTC')"
        return f"toStartOfDay({column})"

    def bucket_start(self, value):
        value = value.replace(minute=0, second=0, microsecond=0)
        if not self.hourly:
            value = value.replace(hour=0)
        if self.monthly:
            return value.replace(day=1)
        return value - timedelta(days=value.weekday()) if self.weekly else value

    async def query(self, sql, **params):
        async with self.slots:
            return await self.client.query(sql, {**self.params, **params})

    def buckets(self):
        step = timedelta(hours=1) if self.hourly else timedelta(days=7 if self.weekly else 1)
        first = self.bucket_start(self.start)
        last = self.bucket_start(min(self.now, self.end - timedelta(microseconds=1)))
        if self.monthly:
            points = []
            while first <= last:
                points.append(first)
                first = (
                    first.replace(year=first.year + 1, month=1)
                    if first.month == 12
                    else first.replace(month=first.month + 1)
                )
            return points
        return [first + index * step for index in range(int((last - first) / step) + 1)]

    def empty_series(self, point_type):
        return {point.isoformat(): point_type(bucket=point.isoformat()) for point in self.buckets()}

    @staticmethod
    def key(value):
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        return parsed.astimezone(UTC).isoformat()

    @staticmethod
    def rollup(name, domain, *, daily=False):
        column = "bucket_day" if daily else "bucket_hour"
        bucket = "toDate(m.bucket)" if daily else "m.bucket"
        upper = END if daily else OBSERVED_END
        return f"""analytics_{name} r INNER JOIN (
            SELECT bucket, generation FROM analytics_bucket_manifest FINAL
            WHERE domain = '{domain}' AND bucket >= {START} AND bucket < {upper}
          ) m ON r.{column} = {bucket} AND r.generation = m.generation
          WHERE r.{column} >= {"toDate(" + START + ")" if daily else START}
            AND r.{column} < {"toDate(" + END + ")" if daily else upper}"""

    @staticmethod
    def gauge(name, domain):
        return f"""analytics_{name} g INNER JOIN (
            SELECT shard, generation FROM analytics_gauge_manifest FINAL WHERE domain = '{domain}'
          ) m ON g.shard = m.shard AND g.generation = m.generation"""

    async def archive(self):
        # Imported aggregate-only history is kept separate from reader sketches.
        # Its counts replace a closed day, rather than being added to its events.
        return await self.query(f"""/* overview:archive */ SELECT day, opens, readers,
            accounts_created, published FROM analytics_audience_archive_daily FINAL
            WHERE day >= toDate({START}) AND day < {{capture_day:Date}} AND day < toDate({END})""")

    async def publishing(self):
        rows, archived = await _gather(
            self.query(f"""/* overview:publishing */ SELECT {self.bucket} AS chart_bucket,
                grouping(chart_bucket) AS total, sum(r.published) AS published,
                sum(r.automated) AS automated, {QUANTILES}(r.delay_seconds_state) AS percentiles
                FROM {self.rollup("publishing_hourly", "publishing")}
                GROUP BY chart_bucket WITH ROLLUP"""),
            self.archive(),
        )
        activity = self.empty_series(PublicationBucket)
        summary = PublicationSummary()
        for row in rows:
            p50, p95 = _percentiles(row)
            if row["total"]:
                summary = PublicationSummary(
                    published=row["published"],
                    automated=row["automated"],
                    p50_seconds=p50,
                    p95_seconds=p95,
                )
            else:
                key = self.key(row["chart_bucket"])
                activity[key] = PublicationBucket(
                    bucket=key,
                    published=row["published"],
                    automated=row["automated"],
                    p50_seconds=p50,
                    p95_seconds=p95,
                )
        if not self.hourly:
            daily = {}
            if (self.weekly or self.monthly) and archived:
                counts = await self.query(f"""/* overview:publication-days */
                    SELECT toDate(r.bucket_hour) AS day, sum(r.published) AS published
                    FROM {self.rollup("publishing_hourly", "publishing")}
                      AND toDate(r.bucket_hour) < {{capture_day:Date}} GROUP BY day""")
                daily = {str(row["day"]): row["published"] for row in counts}
            for row in archived:
                if row["published"] is not None:
                    day = datetime.fromisoformat(str(row["day"])).replace(tzinfo=UTC)
                    key = self.bucket_start(day).isoformat()
                    if key in activity:
                        native = (
                            daily.get(str(row["day"]), 0)
                            if self.weekly or self.monthly
                            else activity[key].published
                        )
                        difference = row["published"] - native
                        if difference:
                            activity[key].automated = None
                            activity[key].p50_seconds = activity[key].p95_seconds = None
                            summary.automated = None
                            summary.p50_seconds = summary.p95_seconds = None
                        summary.published += difference
                        activity[key].published += difference
        summary.automatic_percent = (
            round(100 * summary.automated / summary.published, 1)
            if summary.published and summary.automated is not None
            else None
        )
        return PublishingSection(summary=summary, activity=list(activity.values()))

    async def audience(self):
        health_sums = ", ".join(
            "sum(g." + field + ") AS " + field for field in PersonalizationCounts.model_fields
        )
        rows, gauges, archived = await _gather(
            self.query(f"""/* overview:audience */ SELECT {self.bucket} AS chart_bucket,
                sum(r.opens) AS opens, uniqCombined64Merge(r.readers_state) AS estimated_readers,
                sum(r.accounts_created) AS new_accounts
                FROM {self.rollup("audience_hourly", "audience")} GROUP BY chart_bucket"""),
            self.query(f"""/* overview:feed-health */ SELECT
                {health_sums}
                FROM {self.gauge("feed_gauges", "feed")}"""),
            self.archive(),
        )
        activity = {
            bucket.isoformat(): AudienceBucket(
                bucket=bucket.isoformat(),
                opens=0 if bucket.date() >= self.reader_start else None,
                estimated_readers=0 if bucket.date() >= self.reader_start else None,
            )
            for bucket in self.buckets()
        }
        for row in rows:
            key = self.key(row["chart_bucket"])
            point = activity[key]
            point.new_accounts = row["new_accounts"]
            if datetime.fromisoformat(key).date() >= self.reader_start:
                point.opens, point.estimated_readers = row["opens"], row["estimated_readers"]
        if not self.hourly:
            daily = {}
            if (self.weekly or self.monthly) and archived:
                counts = await self.query(f"""/* overview:audience-days */
                    SELECT toDate(r.bucket_hour) AS day, sum(r.opens) AS opens,
                      sum(r.accounts_created) AS new_accounts
                    FROM {self.rollup("audience_hourly", "audience")}
                      AND toDate(r.bucket_hour) < {{capture_day:Date}} GROUP BY day""")
                daily = {str(row["day"]): row for row in counts}
            for row in archived:
                day = datetime.fromisoformat(str(row["day"])).replace(tzinfo=UTC)
                key = self.bucket_start(day).isoformat()
                if key in activity:
                    point = activity[key]
                    if self.weekly or self.monthly:
                        native = daily.get(str(row["day"]), {})
                        point.opens = (
                            point.opens + row["opens"] - native.get("opens", 0)
                            if point.opens is not None and row["opens"] is not None
                            else None
                        )
                        # Daily estimates cannot be merged into a weekly distinct-reader sketch.
                        point.estimated_readers = None
                        if row["accounts_created"] is not None:
                            point.new_accounts += row["accounts_created"] - native.get(
                                "new_accounts", 0
                            )
                    else:
                        point.opens, point.estimated_readers = row["opens"], row["readers"]
                        if row["accounts_created"] is not None:
                            point.new_accounts = row["accounts_created"]
        health = {
            name: int(gauges[0].get(name) or 0) if gauges else 0
            for name in PersonalizationCounts.model_fields
        }
        return AudienceSection(
            activity=list(activity.values()), personalization=PersonalizationCounts(**health)
        )

    async def processing(self):
        outcomes, throughput = await _gather(
            self.query(f"""/* overview:job-outcomes */ SELECT r.kind AS kind,
                sum(r.completed) AS completed, sum(r.failed) AS failed,
                {QUANTILES}(r.duration_seconds_state) AS percentiles
                FROM {self.rollup("processing_hourly", "processing")}
                  AND r.kind != 'topic-decision' GROUP BY r.kind ORDER BY r.kind"""),
            self.query(f"""/* overview:throughput */ SELECT chart_bucket,
                sum(articles_published) AS articles_published,
                sum(article_analyses) AS article_analyses, sum(topic_decisions) AS topic_decisions
                FROM (
                  SELECT {self.bucket} AS chart_bucket, 0 AS articles_published,
                    sumIf(r.useful, r.kind = 'analysis') AS article_analyses,
                    sumIf(r.useful, r.kind = 'topic-decision') AS topic_decisions
                  FROM {self.rollup("processing_hourly", "processing")} GROUP BY chart_bucket
                  UNION ALL SELECT {self.bucket}, sum(r.published), 0, 0
                  FROM {self.rollup("publishing_hourly", "publishing")} GROUP BY {self.bucket}
                ) GROUP BY chart_bucket ORDER BY chart_bucket"""),
        )
        activity = self.empty_series(ThroughputBucket)
        for row in throughput:
            key = self.key(row.pop("chart_bucket"))
            activity[key] = ThroughputBucket(bucket=key, **row)
        jobs = []
        for row in outcomes:
            p50, p95 = _percentiles(row)
            jobs.append(
                JobOutcome(
                    kind=row["kind"],
                    completed=row["completed"],
                    failed=row["failed"],
                    p50_seconds=p50,
                    p95_seconds=p95,
                )
            )
        return ProcessingSection(
            throughput=list(activity.values()),
            outcomes=jobs,
        )

    async def queues(self):
        rows = await self.query(f"""/* overview:queues */ SELECT g.kind AS kind,
            sum(g.queued) AS queued, sum(g.running) AS running,
            min(g.oldest_queued_at) AS oldest_queued_at
            FROM {self.gauge("queue_gauges", "queues")} GROUP BY g.kind ORDER BY g.kind""")
        return ProcessingSection(queues=[QueuePressure(**row) for row in rows])

    async def inference(self):
        sums = ", ".join(
            f"sum(r.{field}) AS {field}"
            for field in (
                "calls",
                "unreported",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
            )
        )
        base = self.rollup("inference_hourly", "inference")
        rows, breakdowns = await _gather(
            self.query(f"""/* overview:inference */ SELECT {self.bucket} AS chart_bucket,
                grouping(chart_bucket) AS total, {sums}, sum(r.failed) AS failed,
                sum(r.web_searches) AS web_searches, {QUANTILES}(r.duration_ms_state) AS percentiles
                FROM {base} GROUP BY chart_bucket WITH ROLLUP"""),
            self.query(f"""/* overview:usage-breakdown */ WITH breakdown AS (
                  SELECT multiIf(grouping(r.task)=0,'tasks',grouping(r.model)=0,
                    'models','efforts') AS dimension,
                    multiIf(grouping(r.task)=0,r.task,grouping(r.model)=0,
                      r.model,r.effort) AS label,
                    sum(r.calls) AS calls, sum(r.failed) AS failed,
                    sum(r.unreported) AS unreported, sum(r.input_tokens) AS input_tokens,
                    sum(r.output_tokens) AS output_tokens
                  FROM {base} GROUP BY GROUPING SETS ((r.task),(r.model),(r.effort))
                ), ranked AS (
                  SELECT *, row_number() OVER(PARTITION BY dimension
                    ORDER BY input_tokens+output_tokens DESC,label) AS rank FROM breakdown
                ) SELECT dimension, if(rank<=8,label,'Other') AS name,
                  sum(calls) AS calls, sum(failed) AS failed, sum(unreported) AS unreported,
                  sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
                FROM ranked GROUP BY dimension,name
                ORDER BY dimension,name='Other',sum(input_tokens)+sum(output_tokens) DESC,name"""),
        )
        activity = self.empty_series(InferenceBucket)
        summary = InferenceSummary()
        for row in rows:
            counters = {field: row[field] for field in COUNTERS}
            if row["total"]:
                summary = InferenceSummary(**counters)
            else:
                key = self.key(row["chart_bucket"])
                p50, p95 = _percentiles(row)
                activity[key] = InferenceBucket(bucket=key, p50_ms=p50, p95_ms=p95, **counters)
        dimensions: dict[str, list[UsageBreakdown]] = {"tasks": [], "models": [], "efforts": []}
        for row in breakdowns:
            dimensions[row.pop("dimension")].append(UsageBreakdown(**row))
        return InferenceSection(summary=summary, activity=list(activity.values()), **dimensions)

    async def coverage(self):
        sources, topics = await _gather(
            self.query(f"""/* overview:sources */ SELECT toString(p.source_id) AS id,
                coalesce(nullIf(g.name,''),'Unknown source') AS name,
                p.publications AS publications,
                coalesce(g.consecutive_failures,0) AS consecutive_failures,
                sum(p.publications) OVER() AS total FROM (
                  SELECT r.source_id AS source_id, sum(r.published) AS publications
                  FROM {self.rollup("source_daily", "sources", daily=True)} GROUP BY r.source_id
                ) p LEFT JOIN (SELECT g.source_id AS source_id, any(g.name) AS name,
                    max(g.consecutive_failures) AS consecutive_failures
                  FROM {self.gauge("source_gauges", "sources")} GROUP BY g.source_id
                ) g ON g.source_id=p.source_id
                ORDER BY p.publications DESC,name,id LIMIT 10"""),
            self.query(f"""/* overview:topics */ SELECT toString(g.topic_id) AS id, g.name AS name,
                g.followers AS followers, g.inferred_users AS inferred_users,
                coalesce(p.publications,0) AS publications FROM (
                  SELECT g.topic_id AS topic_id, any(g.name) AS name, sum(g.followers) AS followers,
                    sum(g.inferred_users) AS inferred_users
                  FROM {self.gauge("topic_gauges", "topics")} GROUP BY g.topic_id
                ) g LEFT JOIN (
                  SELECT r.topic_id AS topic_id, sum(r.published) AS publications
                  FROM {self.rollup("topic_daily", "topics", daily=True)} GROUP BY r.topic_id
                ) p ON p.topic_id=g.topic_id WHERE g.followers>0 OR g.inferred_users>0
                ORDER BY g.followers DESC,g.inferred_users DESC,g.name,g.topic_id LIMIT 10"""),
        )
        return CoverageSection(
            sources=[SourceCoverage(**row) for row in sources],
            topics=[TopicCoverage(**row) for row in topics],
            source_publications_total=int(sources[0]["total"]) if sources else 0,
        )

    async def section(self, section):
        if section not in ANALYTICAL_GROUPS and section != "queues":
            raise ValueError("Operations require current transactional controls")
        async with asyncio.timeout(20):
            data = await getattr(self, section)()
        return OverviewSectionReport(
            generated_at=self.now,
            days=self.days,
            start=self.window.start,
            end=self.window.inclusive_end,
            granularity=self.granularity,
            data=data,
        )
