"""Durable pipeline metadata shared by dispatch and read-only administration.

Handler names are strings: reading metadata never imports worker execution code.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from devfeed_core.job_logs import JobKind
from devfeed_core.jobs import JOB_TIMEOUT_SECONDS
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    NotificationDelivery,
    ResearchVerificationJob,
    SourceEnrichmentJob,
    TopicAnalysisJob,
)

Job = (
    IngestionJob
    | ArticleEnrichmentJob
    | ArticleImageJob
    | SourceEnrichmentJob
    | ArticleAnalysisJob
    | TopicAnalysisJob
    | ResearchVerificationJob
    | NotificationDelivery
)
PipelineKind = JobKind | Literal["research-verification"]


@dataclass(frozen=True)
class JobDefinition:
    kind: PipelineKind
    model: type[Job]
    handler: str
    queue: str
    event: str
    timeout: int = JOB_TIMEOUT_SECONDS
    admin_visible: bool = True

    def lane_condition(self, relationships: bool | None):
        if relationships is None:
            return None
        if self.model is ResearchVerificationJob:
            return ResearchVerificationJob.relationships.is_(relationships)
        if self.model is TopicAnalysisJob:
            return (
                TopicAnalysisJob.topic_id.is_not(None)
                if relationships
                else TopicAnalysisJob.topic_id.is_(None)
            )
        raise ValueError("Relationship routing requires topic analysis jobs")

    def log_fields(self, job: Job) -> dict:
        fields = {"job_id": job.id}
        for field in ("article_id", "source_id", "topic_id", "proposal_id"):
            if value := getattr(job, field, None):
                fields[field] = value
        return fields


JOB_DEFINITIONS = MappingProxyType(
    {
        definition.kind: definition
        for definition in (
            JobDefinition(
                "ingestion",
                IngestionJob,
                "devfeed_aggregator.tasks.ingest",
                "ingestion",
                "ingestion",
            ),
            JobDefinition(
                "article-enrichment",
                ArticleEnrichmentJob,
                "devfeed_aggregator.article_tasks.enrich_article",
                "ingestion",
                "article_enrichment",
            ),
            JobDefinition(
                "images",
                ArticleImageJob,
                "devfeed_aggregator.image_tasks.enrich_image",
                "ingestion",
                "image",
            ),
            JobDefinition(
                "source-enrichment",
                SourceEnrichmentJob,
                "devfeed_aggregator.source_tasks.enrich_source",
                "ingestion",
                "source_enrichment",
            ),
            JobDefinition(
                "analysis",
                ArticleAnalysisJob,
                "devfeed_aggregator.analysis_tasks.analyze_article",
                "analysis",
                "article_analysis",
            ),
            JobDefinition(
                "topic-analysis",
                TopicAnalysisJob,
                "devfeed_aggregator.topic_analysis_tasks.analyze_topic",
                "analysis",
                "topic_analysis",
                timeout=240,
            ),
            JobDefinition(
                "research-verification",
                ResearchVerificationJob,
                "devfeed_aggregator.research_verification_tasks.verify_research",
                "analysis",
                "research_verification",
                timeout=240,
                admin_visible=False,
            ),
            JobDefinition(
                "notifications",
                NotificationDelivery,
                "devfeed_notifications.delivery.deliver_notification",
                "notifications",
                "notification",
            ),
        )
    }
)


def queue_lanes():
    """Enumerate each durable table/lane once, including relationship-only work."""
    for definition in JOB_DEFINITIONS.values():
        if definition.kind in {"topic-analysis", "research-verification"}:
            yield definition, "analysis", definition.lane_condition(False)
            yield definition, "relationships", definition.lane_condition(True)
        else:
            yield definition, definition.queue, None
