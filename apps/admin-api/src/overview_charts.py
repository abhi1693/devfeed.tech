"""Card-specific public payloads extracted from shared internal report documents."""

from datetime import date, datetime
from typing import Annotated, Literal

from devfeed_core.read_models import ReadModelSnapshot
from pydantic import BaseModel, Field, RootModel

from devfeed_admin_api.automation import AutomationBlocker
from devfeed_admin_api.overview_sections import (
    AudienceBucket,
    InferenceBucket,
    InferenceSummary,
    JobOutcome,
    OverviewSectionReport,
    PersonalizationCounts,
    QueuePressure,
    ReviewQueue,
    SourceCoverage,
    ThroughputBucket,
    TopicCoverage,
    UsageBreakdown,
)

ChartName = Literal[
    "attention",
    "publication-rate",
    "publication-latency",
    "audience-activity",
    "personalization",
    "queue-pressure",
    "throughput",
    "job-outcomes",
    "ai-activity",
    "ai-breakdown",
    "source-output",
    "topic-coverage",
]
CHART_SECTIONS = {
    "attention": "operations",
    "publication-rate": "publishing",
    "publication-latency": "publishing",
    "audience-activity": "audience",
    "personalization": "audience",
    "queue-pressure": "processing",
    "throughput": "processing",
    "job-outcomes": "processing",
    "ai-activity": "inference",
    "ai-breakdown": "inference",
    "source-output": "coverage",
    "topic-coverage": "coverage",
}


class ChartMetadata(BaseModel):
    generated_at: datetime
    days: int
    start: date | None = None
    end: date | None = None
    granularity: Literal["hour", "day", "week", "month"] | None = None
    snapshot: ReadModelSnapshot | None = None


class AttentionData(BaseModel):
    full_automation: bool = False
    reviews: list[ReviewQueue] = Field(default_factory=list)
    source_failures: int = 0
    blockers: list[AutomationBlocker] = Field(default_factory=list)


class PublicationRateSummary(BaseModel):
    published: int = 0
    automatic_percent: float | None = None


class PublicationRateBucket(BaseModel):
    bucket: str
    published: int = 0
    automated: int | None = 0


class PublicationRateData(BaseModel):
    summary: PublicationRateSummary = Field(default_factory=PublicationRateSummary)
    activity: list[PublicationRateBucket] = Field(default_factory=list)


class PublicationLatencySummary(BaseModel):
    p50_seconds: float | None = None
    p95_seconds: float | None = None


class PublicationLatencyBucket(PublicationLatencySummary):
    bucket: str


class PublicationLatencyData(BaseModel):
    summary: PublicationLatencySummary = Field(default_factory=PublicationLatencySummary)
    activity: list[PublicationLatencyBucket] = Field(default_factory=list)


class AudienceActivityData(BaseModel):
    activity: list[AudienceBucket] = Field(default_factory=list)


class PersonalizationData(BaseModel):
    personalization: PersonalizationCounts = Field(default_factory=PersonalizationCounts)


class QueuePressureData(BaseModel):
    queues: list[QueuePressure] = Field(default_factory=list)


class ThroughputData(BaseModel):
    throughput: list[ThroughputBucket] = Field(default_factory=list)


class JobOutcomesData(BaseModel):
    outcomes: list[JobOutcome] = Field(default_factory=list)


class AIActivityData(BaseModel):
    summary: InferenceSummary = Field(default_factory=InferenceSummary)
    activity: list[InferenceBucket] = Field(default_factory=list)


class AIBreakdownData(BaseModel):
    tasks: list[UsageBreakdown] = Field(default_factory=list)
    models: list[UsageBreakdown] = Field(default_factory=list)
    efforts: list[UsageBreakdown] = Field(default_factory=list)


class SourceOutputData(BaseModel):
    sources: list[SourceCoverage] = Field(default_factory=list)
    source_publications_total: int = 0


class TopicCoverageData(BaseModel):
    topics: list[TopicCoverage] = Field(default_factory=list)


class AttentionChartReport(ChartMetadata):
    chart: Literal["attention"] = "attention"
    data: AttentionData


class PublicationRateChartReport(ChartMetadata):
    chart: Literal["publication-rate"] = "publication-rate"
    data: PublicationRateData


class PublicationLatencyChartReport(ChartMetadata):
    chart: Literal["publication-latency"] = "publication-latency"
    data: PublicationLatencyData


class AudienceActivityChartReport(ChartMetadata):
    chart: Literal["audience-activity"] = "audience-activity"
    data: AudienceActivityData


class PersonalizationChartReport(ChartMetadata):
    chart: Literal["personalization"] = "personalization"
    data: PersonalizationData


class QueuePressureChartReport(ChartMetadata):
    chart: Literal["queue-pressure"] = "queue-pressure"
    data: QueuePressureData


class ThroughputChartReport(ChartMetadata):
    chart: Literal["throughput"] = "throughput"
    data: ThroughputData


class JobOutcomesChartReport(ChartMetadata):
    chart: Literal["job-outcomes"] = "job-outcomes"
    data: JobOutcomesData


class AIActivityChartReport(ChartMetadata):
    chart: Literal["ai-activity"] = "ai-activity"
    data: AIActivityData


class AIBreakdownChartReport(ChartMetadata):
    chart: Literal["ai-breakdown"] = "ai-breakdown"
    data: AIBreakdownData


class SourceOutputChartReport(ChartMetadata):
    chart: Literal["source-output"] = "source-output"
    data: SourceOutputData


class TopicCoverageChartReport(ChartMetadata):
    chart: Literal["topic-coverage"] = "topic-coverage"
    data: TopicCoverageData


ChartResult = Annotated[
    AttentionChartReport
    | PublicationRateChartReport
    | PublicationLatencyChartReport
    | AudienceActivityChartReport
    | PersonalizationChartReport
    | QueuePressureChartReport
    | ThroughputChartReport
    | JobOutcomesChartReport
    | AIActivityChartReport
    | AIBreakdownChartReport
    | SourceOutputChartReport
    | TopicCoverageChartReport,
    Field(discriminator="chart"),
]


class OverviewChartReport(RootModel[ChartResult]):
    pass


def chart_report(chart: ChartName, report: OverviewSectionReport) -> OverviewChartReport:
    """Serialization validates the card contract and drops unrelated section fields."""
    return OverviewChartReport.model_validate({"chart": chart, **report.model_dump(mode="python")})
