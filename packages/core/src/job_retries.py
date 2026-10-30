"""Explicit retries use current eligibility and the pipelines' coalescing requests."""

from __future__ import annotations

import uuid
from typing import Literal, cast, overload

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.analysis import request_analysis
from devfeed_core.article_jobs import retry_article
from devfeed_core.article_refresh_jobs import ArticleJobModel, refresh_retry_condition
from devfeed_core.config import get_settings
from devfeed_core.image_jobs import retry_image
from devfeed_core.job_definitions import Job, PipelineKind
from devfeed_core.json_types import JsonRecord
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    NotificationDelivery,
    SourceEnrichmentJob,
    TopicAnalysisJob,
    utcnow,
)
from devfeed_core.notification_delivery import DELIVERY_POLICY
from devfeed_core.services import OperationConflict, RecordNotFound, retry_job
from devfeed_core.source_enrichment import request_enrichment
from devfeed_core.topic_analysis import request_topic_analysis
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    request_relationship_analysis,
)


def retry_candidate(model: type[Job]) -> ColumnElement[bool]:
    """Only the latest failed run per subject is an unresolved failure.

    Keep historical failures visible, but never offer them again once another run
    exists. An active run also blocks retry even if timestamps are out of order.
    Notification retries reuse their original row and idempotency key instead.
    """
    failed = model.status == "failed"
    if model is NotificationDelivery:
        return failed
    newer: type[Job]
    if model is ArticleImageJob:
        newer = aliased(ArticleImageJob)
        same_subject = or_(
            and_(
                ArticleImageJob.article_id.is_not(None),
                newer.article_id == ArticleImageJob.article_id,
                or_(
                    newer.status.in_(("queued", "running")),
                    and_(
                        newer.operation == ArticleImageJob.operation,
                        or_(
                            and_(
                                ArticleImageJob.operation == "store",
                                newer.image_url.is_not_distinct_from(ArticleImageJob.image_url),
                            ),
                            and_(
                                ArticleImageJob.operation == "discover",
                                or_(
                                    ArticleImageJob.storage["input_url"].as_string().is_(None),
                                    newer.storage["input_url"]
                                    .as_string()
                                    .is_not_distinct_from(
                                        ArticleImageJob.storage["input_url"].as_string()
                                    ),
                                ),
                                or_(
                                    ArticleImageJob.storage["article_refresh"]
                                    .as_boolean()
                                    .is_not(True),
                                    newer.storage["previous_image_url"]
                                    .as_string()
                                    .is_not_distinct_from(
                                        ArticleImageJob.storage["previous_image_url"].as_string()
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            and_(
                ArticleImageJob.source_id.is_not(None), newer.source_id == ArticleImageJob.source_id
            ),
            and_(ArticleImageJob.topic_id.is_not(None), newer.topic_id == ArticleImageJob.topic_id),
            and_(
                ArticleImageJob.product_id.is_not(None),
                newer.product_id == ArticleImageJob.product_id,
                newer.operation == ArticleImageJob.operation,
                newer.account_id.is_not_distinct_from(ArticleImageJob.account_id),
            ),
        )
        same_subject = and_(
            same_subject, newer.refresh_id.is_not_distinct_from(ArticleImageJob.refresh_id)
        )
    elif model is TopicAnalysisJob:
        newer = aliased(TopicAnalysisJob)
        same_subject = or_(
            and_(
                TopicAnalysisJob.proposal_id.is_not(None),
                newer.proposal_id == TopicAnalysisJob.proposal_id,
            ),
            and_(
                TopicAnalysisJob.topic_id.is_not(None), newer.topic_id == TopicAnalysisJob.topic_id
            ),
        )
    else:
        newer = aliased(model)
        subject = "article_id" if hasattr(model, "article_id") else "source_id"
        same_subject = getattr(newer, subject) == getattr(model, subject)
        if model in {ArticleAnalysisJob, ArticleEnrichmentJob}:
            same_subject = and_(
                same_subject,
                cast(ArticleJobModel, newer).refresh_id.is_not_distinct_from(
                    cast(ArticleJobModel, model).refresh_id
                ),
            )
    superseded = (
        select(newer.id)
        .where(
            same_subject,
            newer.id != model.id,
            or_(
                newer.status.in_(("queued", "running")),
                tuple_(newer.created_at, newer.id) > tuple_(model.created_at, model.id),
            ),
        )
        .correlate(model)
        .exists()
    )
    eligibility = and_(failed, ~superseded)
    if model is ArticleImageJob:
        current_article = (
            select(Article.id)
            .where(
                Article.id == ArticleImageJob.article_id,
                Article.publication_status != "published",
                or_(
                    and_(
                        ArticleImageJob.operation == "store",
                        Article.image_url == ArticleImageJob.image_url,
                    ),
                    and_(
                        ArticleImageJob.operation == "discover",
                        or_(
                            ArticleImageJob.storage["input_url"].as_string().is_(None),
                            ArticleImageJob.storage["input_url"].as_string()
                            == Article.canonical_url,
                        ),
                        or_(
                            and_(
                                ArticleImageJob.storage["article_refresh"]
                                .as_boolean()
                                .is_not(True),
                                Article.image_url.is_(None),
                            ),
                            and_(
                                ArticleImageJob.storage["article_refresh"].as_boolean().is_(True),
                                ArticleImageJob.storage["input_url"].as_string()
                                == Article.canonical_url,
                                ArticleImageJob.storage["previous_image_url"]
                                .as_string()
                                .is_not_distinct_from(Article.image_url),
                            ),
                        ),
                    ),
                ),
            )
            .correlate(model)
            .exists()
        )
        eligibility = and_(
            eligibility,
            or_(
                and_(
                    ArticleImageJob.refresh_id.is_(None),
                    or_(ArticleImageJob.article_id.is_(None), current_article),
                ),
                refresh_retry_condition(ArticleImageJob),
            ),
        )
    elif model in {ArticleAnalysisJob, ArticleEnrichmentJob}:
        article_model = cast(ArticleJobModel, model)
        ordinary_article = (
            select(Article.id)
            .where(
                Article.id == article_model.article_id,
                Article.publication_status != "published",
            )
            .correlate(model)
            .exists()
        )
        eligibility = and_(
            eligibility,
            or_(
                and_(article_model.refresh_id.is_(None), ordinary_article),
                refresh_retry_condition(article_model),
            ),
        )
    return eligibility


def retry_notification(session: Session, identifier: uuid.UUID) -> NotificationDelivery:
    job = session.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise RecordNotFound("Notification delivery not found")
    if job.status != "failed":
        raise OperationConflict("Only failed deliveries can be retried")
    if DELIVERY_POLICY.expired(job, utcnow()):
        raise OperationConflict(
            "Delivery is beyond the safe retry window; review it before publishing a new event"
        )
    # Keep the original delivery ID and event key for Chimely's idempotency check.
    job.status, job.attempts, job.available_at = "queued", 0, utcnow()
    job.dispatched_at = job.finished_at = job.lease_until = job.lease_token = job.error = None
    session.flush()
    return job


def preserve_analysis_steps(previous: ArticleAnalysisJob, successor: ArticleAnalysisJob) -> None:
    """Retain independently validated specialist outputs for the exact same private subject."""
    if (
        previous.steps
        and successor.id != previous.id
        and successor.refresh_id == previous.refresh_id
        and successor.input_hash == previous.input_hash
        and successor.status == "queued"
        and not successor.attempts
        and not successor.steps
    ):
        successor.steps = {
            name: dict(step)
            for name, step in previous.steps.items()
            if isinstance(step, dict) and step.get("status") == "succeeded"
        }


@overload
def retry_failed_job(
    session: Session,
    job: ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob,
    kind: Literal["analysis", "article-enrichment", "images"],
    actor: JsonRecord,
) -> ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob | None: ...


@overload
def retry_failed_job(
    session: Session, job: Job, kind: PipelineKind, actor: JsonRecord
) -> Job | None: ...


def retry_failed_job(
    session: Session, job: Job, kind: PipelineKind, actor: JsonRecord
) -> Job | None:
    if job.status != "failed":
        raise OperationConflict("Only failed jobs can be retried")
    model = type(job)
    if not session.scalar(select(model.id).where(model.id == job.id, retry_candidate(model))):
        raise OperationConflict(
            "This run has been superseded. Retry the latest failed run instead."
        )
    if kind in {"analysis", "topic-analysis"} and not get_settings().ai_enabled:
        raise OperationConflict("Configure and enable AI before retrying analysis")
    # Request functions serialize on the subject and coalesce active runs. Do not
    # lock the failed job before them: workers acquire job and subject locks too.
    if kind == "ingestion":
        return retry_job(session, job.id)
    if kind == "article-enrichment":
        return retry_article(session, job.id)
    if kind == "images":
        result = retry_image(session, job.id)
        if result is None:
            raise OperationConflict("The article already has an image; no retry is needed")
        return result
    if kind == "source-enrichment":
        source_job = cast(SourceEnrichmentJob, job)
        if getattr(source_job, "operation", "legacy") == "relevance":
            from devfeed_core.source_enrichment import request_relevance

            return request_relevance(session, source_job.source_id)
        return request_enrichment(session, source_job.source_id)
    if kind == "analysis":
        analysis_job = cast(ArticleAnalysisJob, job)
        successor = (
            request_analysis(session, analysis_job.article_id, refresh_id=analysis_job.refresh_id)
            if analysis_job.refresh_id is not None
            else request_analysis(session, analysis_job.article_id)
        )
        preserve_analysis_steps(analysis_job, successor)
        return successor
    if kind == "topic-analysis":
        topic_job = cast(TopicAnalysisJob, job)
        if topic_job.proposal_id:
            return request_topic_analysis(session, topic_job.proposal_id, actor)
        # Retain a targeted relationship query rather than broadening its scope.
        assert topic_job.topic_id is not None  # Relationship jobs have a topic subject.
        return request_relationship_analysis(
            session,
            topic_job.topic_id,
            RelationshipAnalysisRequest(
                related_topic_id=topic_job.input_snapshot.get("related_topic_id")
            ),
            actor,
        )
    if kind == "notifications":
        return retry_notification(session, job.id)
    raise OperationConflict("Unsupported job type")
