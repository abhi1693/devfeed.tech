"""Compact Overview section contracts shared by publication and the admin reader."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from devfeed_core.analytics_observation import AnalyticsSnapshot
from pydantic import BaseModel, Field

from devfeed_admin_api.automation import AutomationBlocker

SectionName = Literal["operations", "publishing", "audience", "processing", "inference", "coverage"]


class ReviewQueue(BaseModel):
    kind: Literal["articles", "sources", "topics", "relationships"]
    count: int = 0
    oldest_at: datetime | None = None


class OperationsSection(BaseModel):
    section: Literal["operations"] = "operations"
    full_automation: bool = False
    reviews: list[ReviewQueue] = Field(default_factory=list)
    source_failures: int = 0
    blockers: list[AutomationBlocker] = Field(default_factory=list)


class PublicationBucket(BaseModel):
    bucket: str
    published: int = 0
    automated: int | None = 0
    p50_seconds: float | None = None
    p95_seconds: float | None = None


class PublicationSummary(BaseModel):
    published: int = 0
    automated: int | None = 0
    automatic_percent: float | None = None
    p50_seconds: float | None = None
    p95_seconds: float | None = None


class PublishingSection(BaseModel):
    section: Literal["publishing"] = "publishing"
    summary: PublicationSummary = Field(default_factory=PublicationSummary)
    activity: list[PublicationBucket] = Field(default_factory=list)


class AudienceBucket(BaseModel):
    bucket: str
    opens: int | None = None
    estimated_readers: float | None = None
    new_accounts: int = 0


class PersonalizationCounts(BaseModel):
    ready: int = 0
    refreshing: int = 0
    expired: int = 0
    pending: int = 0
    not_needed: int = 0
    overdue: int = 0
    empty_with_interests: int = 0


class AudienceSection(BaseModel):
    section: Literal["audience"] = "audience"
    activity: list[AudienceBucket] = Field(default_factory=list)
    personalization: PersonalizationCounts = Field(default_factory=PersonalizationCounts)


class QueuePressure(BaseModel):
    kind: str
    queued: int = 0
    running: int = 0
    oldest_queued_at: datetime | None = None


class ThroughputBucket(BaseModel):
    bucket: str
    articles_published: int = 0
    article_analyses: int = 0
    topic_decisions: int = 0


class JobOutcome(BaseModel):
    kind: str
    completed: int = 0
    failed: int = 0
    p50_seconds: float | None = None
    p95_seconds: float | None = None


class ProcessingSection(BaseModel):
    section: Literal["processing"] = "processing"
    queues: list[QueuePressure] = Field(default_factory=list)
    throughput: list[ThroughputBucket] = Field(default_factory=list)
    outcomes: list[JobOutcome] = Field(default_factory=list)


class InferenceBucket(BaseModel):
    bucket: str
    calls: int = 0
    failed: int = 0
    unreported: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    web_searches: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None


class UsageBreakdown(BaseModel):
    name: str
    calls: int = 0
    failed: int = 0
    unreported: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class InferenceSummary(BaseModel):
    calls: int = 0
    failed: int = 0
    unreported: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    web_searches: int = 0


class InferenceSection(BaseModel):
    section: Literal["inference"] = "inference"
    summary: InferenceSummary = Field(default_factory=InferenceSummary)
    activity: list[InferenceBucket] = Field(default_factory=list)
    tasks: list[UsageBreakdown] = Field(default_factory=list)
    models: list[UsageBreakdown] = Field(default_factory=list)
    efforts: list[UsageBreakdown] = Field(default_factory=list)


class SourceCoverage(BaseModel):
    id: UUID
    name: str
    publications: int = 0
    consecutive_failures: int = 0


class TopicCoverage(BaseModel):
    id: UUID
    name: str
    followers: int = 0
    inferred_users: int = 0
    publications: int = 0


class CoverageSection(BaseModel):
    section: Literal["coverage"] = "coverage"
    sources: list[SourceCoverage] = Field(default_factory=list)
    topics: list[TopicCoverage] = Field(default_factory=list)
    source_publications_total: int = 0


SectionData = Annotated[
    OperationsSection
    | PublishingSection
    | AudienceSection
    | ProcessingSection
    | InferenceSection
    | CoverageSection,
    Field(discriminator="section"),
]


class OverviewSectionReport(BaseModel):
    generated_at: datetime
    days: int
    start: date | None = None
    end: date | None = None
    granularity: Literal["hour", "day", "week", "month"] | None = None
    snapshot: AnalyticsSnapshot | None = None
    data: SectionData
