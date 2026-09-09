"""Explicit retries use current eligibility and the pipelines' coalescing requests."""

from datetime import timedelta

from sqlalchemy import and_, case, or_, select, tuple_
from sqlalchemy.orm import aliased

from devfeed_core.analysis import request_analysis
from devfeed_core.article_jobs import retry_article
from devfeed_core.config import get_settings
from devfeed_core.image_jobs import retry_image
from devfeed_core.models import NotificationDelivery, TopicAnalysisJob, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound, retry_job
from devfeed_core.source_enrichment import request_enrichment
from devfeed_core.topic_analysis import request_topic_analysis
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    request_relationship_analysis,
)


def retry_candidate(model):
    """Only the latest failed run per subject is an unresolved failure.

    Keep historical failures visible, but never offer them again once another run
    exists. An active run also blocks retry even if timestamps are out of order.
    Notification retries reuse their original row and idempotency key instead.
    """
    failed = model.status == "failed"
    if model is NotificationDelivery:
        return failed
    newer = aliased(model)
    if model is TopicAnalysisJob:
        same_subject = or_(
            and_(model.proposal_id.is_not(None), newer.proposal_id == model.proposal_id),
            and_(model.topic_id.is_not(None), newer.topic_id == model.topic_id),
        )
    else:
        subject = "article_id" if hasattr(model, "article_id") else "source_id"
        same_subject = getattr(newer, subject) == getattr(model, subject)
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
    return and_(failed, ~superseded)


def job_display_status(model):
    return case(
        (and_(model.status == "failed", ~retry_candidate(model)), "retried"),
        else_=model.status,
    )


def retry_notification(session, identifier):
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
    if utcnow() - job.created_at >= timedelta(days=28):
        raise OperationConflict(
            "Delivery is beyond the safe retry window; review it before publishing a new event"
        )
    # Keep the original delivery ID and event key for Chimely's idempotency check.
    job.status, job.attempts, job.available_at = "queued", 0, utcnow()
    job.dispatched_at = job.finished_at = job.lease_until = job.lease_token = job.error = None
    session.flush()
    return job


def retry_failed_job(session, job, kind, actor):
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
        return request_enrichment(session, job.source_id)
    if kind == "analysis":
        return request_analysis(session, job.article_id)
    if kind == "topic-analysis":
        if job.proposal_id:
            return request_topic_analysis(session, job.proposal_id, actor)
        # Retain a targeted relationship query rather than broadening its scope.
        return request_relationship_analysis(
            session,
            job.topic_id,
            RelationshipAnalysisRequest(
                related_topic_id=job.input_snapshot.get("related_topic_id")
            ),
            actor,
        )
    if kind == "notifications":
        return retry_notification(session, job.id)
    raise OperationConflict("Unsupported job type")
