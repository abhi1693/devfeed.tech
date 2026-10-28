"""Overview queries over application-published ClickHouse observations."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Literal, TypeVar, get_args

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable, SqlRecord
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.analytics_overview_direct import DirectOverviewReader
from pydantic import BaseModel

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
    SectionData,
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


Point = TypeVar("Point", bound=BaseModel)


async def _gather(*coroutines: Coroutine[None, None, list[SqlRecord]]) -> list[list[SqlRecord]]:
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _number(value: int | float | str | None) -> float | None:
    return float(value) if value is not None and math.isfinite(float(value)) else None


def _percentiles(row: SqlRecord) -> tuple[float | None, float | None]:
    values = row.get("percentiles") or [None, None]
    return _number(values[0]), _number(values[1])


class _ReportQueries:
    def __init__(self, report: Reports) -> None:
        self.report = report

    async def query(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> list[SqlRecord]:
        return await self.report.query(statement, **(parameters or {}))


class Reports:
    def __init__(
        self,
        client: AnalyticsClient,
        window: ReportWindow,
        now: datetime,
        *,
        capture_started_at: datetime,
    ) -> None:
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
        self.observed_end = self.now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        self.params: dict[str, object] = {
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
        self.granularity: Literal["hour", "month", "week", "day"] = (
            "hour" if self.hourly else "month" if self.monthly else "week" if self.weekly else "day"
        )
        self.slots = asyncio.Semaphore(2)
        self.direct = DirectOverviewReader(_ReportQueries(self))

    def bucket_start(self, value: datetime) -> datetime:
        value = value.replace(minute=0, second=0, microsecond=0)
        if not self.hourly:
            value = value.replace(hour=0)
        if self.monthly:
            return value.replace(day=1)
        return value - timedelta(days=value.weekday()) if self.weekly else value

    async def query(self, sql: str, **params: object) -> list[SqlRecord]:
        async with self.slots:
            return await self.client.query(sql, {**self.params, **params})

    def buckets(self) -> list[datetime]:
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

    def empty_series(self, point_type: type[Point]) -> dict[str, Point]:
        return {point.isoformat(): point_type(bucket=point.isoformat()) for point in self.buckets()}

    @staticmethod
    def key(value: datetime | str) -> str:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        return parsed.astimezone(UTC).isoformat()

    async def archive(self) -> list[SqlRecord]:
        # Imported aggregate-only history is kept separate from reader sketches.
        # Its counts replace a closed day, rather than being added to its events.
        return await self.query(f"""/* overview:archive */ SELECT day, opens, readers,
            accounts_created, published FROM analytics_audience_archive_daily FINAL
            WHERE day >= toDate({START}) AND day < {{capture_day:Date}} AND day < toDate({END})""")

    async def publishing(self) -> PublishingSection:
        rows, archived = await _gather(
            self.direct.publication_overview(
                self.start, min(self.end, self.observed_end), granularity=self.granularity
            ),
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
                counts = await self.direct.publication_days(
                    self.start, min(self.end, datetime.combine(self.capture_day, time.min, UTC))
                )
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

    async def audience(self) -> AudienceSection:
        rows, gauges, archived = await _gather(
            self.direct.audience(
                self.start, min(self.end, self.observed_end), granularity=self.granularity
            ),
            self.direct.feed_health(self.now),
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
                counts = await self.direct.audience(
                    self.start,
                    min(self.end, datetime.combine(self.capture_day, time.min, UTC)),
                    daily=True,
                )
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

    async def processing(self) -> ProcessingSection:
        outcomes, throughput = await _gather(
            self.direct.job_outcomes(self.start, min(self.end, self.observed_end)),
            self.direct.throughput(
                self.start, min(self.end, self.observed_end), granularity=self.granularity
            ),
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

    async def queues(self) -> ProcessingSection:
        rows = await self.direct.queues()
        return ProcessingSection(queues=[QueuePressure(**row) for row in rows])

    async def inference(self) -> InferenceSection:
        ready = await self.query("""/* overview:inference-ready */ SELECT complete
            FROM analytics_backfill_progress FINAL WHERE import_id='direct-v1'
            AND source='inference_calls'""")
        if len(ready) != 1 or int(ready[0].get("complete", 0)) != 1:
            raise AnalyticsUnavailable("Inference history is still importing")
        rows = await InferenceStore(self.client).aggregate(
            self.start, min(self.end, self.observed_end), granularity=self.granularity
        )
        activity = self.empty_series(InferenceBucket)
        summary = InferenceSummary()
        dimensions: dict[str, list[UsageBreakdown]] = {"tasks": [], "models": [], "efforts": []}
        for row in rows:
            dimension = row["dimension"]
            counters = {field: row[field] for field in COUNTERS}
            if dimension == "summary":
                summary = InferenceSummary(**counters)
            elif dimension == "activity":
                key = self.key(row["chart_bucket"])
                p50, p95 = _percentiles(row)
                activity[key] = InferenceBucket(bucket=key, p50_ms=p50, p95_ms=p95, **counters)
            elif dimension in dimensions:
                dimensions[dimension].append(
                    UsageBreakdown(
                        name=row["name"],
                        calls=row["calls"],
                        failed=row["failed"],
                        unreported=row["unreported"],
                        input_tokens=row["input_tokens"],
                        output_tokens=row["output_tokens"],
                    )
                )
        return InferenceSection(summary=summary, activity=list(activity.values()), **dimensions)

    async def coverage(self) -> CoverageSection:
        sources, topics = await _gather(
            self.direct.source_coverage(self.start, self.end),
            self.direct.topic_coverage(self.start, self.end),
        )
        return CoverageSection(
            sources=[SourceCoverage(**row) for row in sources],
            topics=[TopicCoverage(**row) for row in topics],
            source_publications_total=int(sources[0]["total"]) if sources else 0,
        )

    async def section(self, section: SectionName | Literal["queues"]) -> OverviewSectionReport:
        if section not in ANALYTICAL_GROUPS and section != "queues":
            raise ValueError("Operations require current transactional controls")
        async with asyncio.timeout(20):
            readers: dict[str, Callable[[], Awaitable[SectionData]]] = {
                "publishing": self.publishing,
                "audience": self.audience,
                "processing": self.processing,
                "queues": self.queues,
                "inference": self.inference,
                "coverage": self.coverage,
            }
            data = await readers[section]()
        return OverviewSectionReport(
            generated_at=self.now,
            days=self.days,
            start=self.window.start,
            end=self.window.inclusive_end,
            granularity=self.granularity,
            data=data,
        )
