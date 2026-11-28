"""Bounded operator card contracts shared by native publishing and admin APIs."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from devfeed_core.analytics_observation import AnalyticsSnapshot
from devfeed_core.article_processing_retry import RetryTarget
from devfeed_core.json_types import JsonValue
from devfeed_core.schemas import ContentFormat, ContentType, InputModel, ORMModel
from devfeed_core.source_types import SourceType

Stage = Literal[
    "ingested", "enrichment", "analysis", "images", "review", "ready", "published", "rejected"
]


STAGES: tuple[Stage, ...] = (
    "ingested",
    "enrichment",
    "analysis",
    "images",
    "review",
    "ready",
    "published",
    "rejected",
)


class PipelineJob(ORMModel):
    id: uuid.UUID
    status: str
    outcome: str | None
    available_at: datetime
    finished_at: datetime | None
    retryable: bool


class PipelineJobs(ORMModel):
    enrichment: PipelineJob | None = None
    analysis: PipelineJob | None = None
    images: PipelineJob | None = None


class MaintenanceTopic(ORMModel):
    id: uuid.UUID
    name: str
    role: str


class MaintenanceTag(ORMModel):
    id: uuid.UUID
    name: str


class MaintenancePreview(ORMModel):
    title: str
    summary: str
    description: str | None
    image_url: str | None
    language: str | None
    content_type: ContentType
    content_format: ContentFormat
    topics: list[MaintenanceTopic]
    tags: list[MaintenanceTag]


class MaintenanceDetail(ORMModel):
    id: uuid.UUID
    article_id: uuid.UUID
    target: RetryTarget
    status: Literal["active", "review", "promoted", "superseded", "cancelled"]
    version: int
    created_at: datetime
    completed_at: datetime | None
    ready: bool
    reasons: list[str]
    allowed_actions: list[Literal["approve", "reject"]]
    jobs: PipelineJobs
    retry_targets: list[RetryTarget]
    replacement: MaintenancePreview


class PipelineCard(ORMModel):
    id: uuid.UUID
    title: str
    source_name: str | None
    source_id: uuid.UUID | None
    discovered_at: datetime
    stage: Stage
    state: str
    blockers: list[str]
    next_check_at: datetime | None
    editorial_revision: int
    jobs: PipelineJobs
    allowed_actions: list[str] = Field(default_factory=list)
    current_analysis_step: str | None = None
    retry_targets: list[RetryTarget] = Field(default_factory=list)


class PipelineSourceGroup(ORMModel):
    source_id: uuid.UUID | None
    source_name: str | None
    total: int
    state_counts: dict[str, int]


class PipelineColumn(ORMModel):
    stage: Stage
    total: int
    items: list[PipelineCard]
    next_cursor: int | None
    source_groups: list[PipelineSourceGroup] = Field(default_factory=list)


class PipelineBoard(ORMModel):
    as_of: datetime
    terminal_since: datetime
    counts: dict[str, int]
    columns: list[PipelineColumn]
    snapshot: AnalyticsSnapshot | None = None


class PipelineCheck(ORMModel):
    code: str
    label: str
    status: Literal["passed", "pending", "blocked"]
    detail: str


class AnalysisStepOut(ORMModel):
    name: str
    label: str
    status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    prompt_version: str
    attempts: int = Field(ge=0)
    error: str | None = None
    available_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PipelineDetail(ORMModel):
    article_id: uuid.UUID
    editorial_revision: int
    stage: Stage
    state: str
    checks: list[PipelineCheck]
    blockers: list[str]
    can_publish: bool
    jobs: PipelineJobs
    allowed_actions: list[str]
    advance_label: str | None
    analysis_steps: list[AnalysisStepOut] = Field(default_factory=list)
    retry_targets: list[RetryTarget] = Field(default_factory=list)
    maintenance: MaintenanceDetail | None = None


class AdvancePipeline(InputModel):
    expected_revision: int = Field(ge=0)


class RetryPipeline(InputModel):
    expected_revision: int = Field(ge=0)
    target: RetryTarget
    expected_job_id: uuid.UUID | None


SourcePipelineStage = Literal[
    "discovery",
    "profile",
    "assessment",
    "branding",
    "review",
    "ingestion",
    "active",
    "paused",
    "rejected",
]


SourcePipelineState = Literal["waiting", "running", "retry_wait", "blocked", "ready", "complete"]


SourcePipelineKind = Literal["source", "candidate"]


SourcePipelineTarget = Literal["discovery", "profile", "assessment", "branding", "ingestion"]


SOURCE_STAGES: tuple[SourcePipelineStage, ...] = (
    "discovery",
    "profile",
    "assessment",
    "branding",
    "review",
    "ingestion",
    "active",
    "paused",
    "rejected",
)


SOURCE_TERMINAL_STAGES = frozenset({"active", "paused", "rejected"})


class SourcePipelineRetryTarget(ORMModel):
    stage: SourcePipelineTarget
    job_id: uuid.UUID | None = None


class SourcePipelineCard(ORMModel):
    key: str
    kind: SourcePipelineKind
    id: uuid.UUID
    source_id: uuid.UUID | None
    candidate_id: uuid.UUID | None
    name: str
    website_url: str | None
    feed_url: str | None
    source_type: SourceType
    approval_status: Literal["pending", "approved", "rejected"]
    enabled: bool
    stage: SourcePipelineStage
    state: SourcePipelineState
    created_at: datetime
    updated_at: datetime
    snapshot: str
    can_advance: bool
    can_approve: bool
    can_ingest: bool
    next_action: str | None
    advance_label: str | None
    retry_targets: list[SourcePipelineRetryTarget]


class SourcePipelineColumn(ORMModel):
    stage: SourcePipelineStage
    total: int
    items: list[SourcePipelineCard]
    next_cursor: int | None


class SourcePipelineBoard(ORMModel):
    as_of: datetime
    terminal_since: datetime
    counts: dict[SourcePipelineStage, int]
    columns: list[SourcePipelineColumn]
    snapshot: AnalyticsSnapshot | None = None


class SourcePipelineCheck(ORMModel):
    key: str
    status: str
    job_id: uuid.UUID | None = None
    error: str | None = None
    available_at: datetime | None = None


class SourcePipelineTask(ORMModel):
    stage: SourcePipelineTarget
    job_id: uuid.UUID
    status: str
    attempts: int
    created_at: datetime
    available_at: datetime
    finished_at: datetime | None
    error: str | None


class SourcePipelineProfile(ORMModel):
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    image_url: str | None = None
    language: str | None = None


class SourcePipelineReview(ORMModel):
    decision: str
    actor: str | None
    note: str | None
    created_at: datetime


class SourcePipelineDetail(ORMModel):
    item: SourcePipelineCard
    checks: list[SourcePipelineCheck]
    blockers: list[str]
    tasks: list[SourcePipelineTask]
    profile: SourcePipelineProfile
    assessment: dict[str, JsonValue] | None
    reviews: list[SourcePipelineReview]
    candidate_ids: list[uuid.UUID]


class SourcePipelineAdvance(InputModel):
    expected_snapshot: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")


class SourcePipelineRetry(SourcePipelineAdvance):
    target: SourcePipelineTarget
    job_id: uuid.UUID | None = None
