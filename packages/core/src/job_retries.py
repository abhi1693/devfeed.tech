"""Explicit retries use current eligibility and the pipelines' coalescing requests."""

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import aliased

from devfeed_core.analysis import request_analysis
from devfeed_core.article_jobs import retry_article
from devfeed_core.config import get_settings
from devfeed_core.image_jobs import retry_image
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    NotificationDelivery,
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
    if model is ArticleImageJob:
        same_subject = or_(
            and_(
                model.article_id.is_not(None),
                newer.article_id == model.article_id,
                or_(
                    newer.status.in_(("queued", "running")),
                    and_(
                        newer.operation == model.operation,
                        or_(
                            and_(
                                model.operation == "store",
                                newer.image_url.is_not_distinct_from(model.image_url),
                            ),
                            and_(
                                model.operation == "discover",
                                or_(
                                    model.storage["input_url"].as_string().is_(None),
                                    newer.storage["input_url"]
                                    .as_string()
                                    .is_not_distinct_from(model.storage["input_url"].as_string()),
                                ),
                                or_(
                                    model.storage["article_refresh"].as_boolean().is_not(True),
                                    newer.storage["previous_image_url"]
                                    .as_string()
                                    .is_not_distinct_from(
                                        model.storage["previous_image_url"].as_string()
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            and_(model.source_id.is_not(None), newer.source_id == model.source_id),
            and_(model.topic_id.is_not(None), newer.topic_id == model.topic_id),
            and_(
                model.product_id.is_not(None),
                newer.product_id == model.product_id,
                newer.operation == model.operation,
                newer.account_id.is_not_distinct_from(model.account_id),
            ),
        )
    elif model is TopicAnalysisJob:
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
    eligibility = and_(failed, ~superseded)
    if model is ArticleImageJob:
        current_article = (
            select(Article.id)
            .where(
                Article.id == model.article_id,
                Article.publication_status != "published",
                or_(
                    and_(
                        model.operation == "store",
                        Article.image_url == model.image_url,
                    ),
                    and_(
                        model.operation == "discover",
                        or_(
                            model.storage["input_url"].as_string().is_(None),
                            model.storage["input_url"].as_string() == Article.canonical_url,
                        ),
                        or_(
                            and_(
                                model.storage["article_refresh"].as_boolean().is_not(True),
                                Article.image_url.is_(None),
                            ),
                            and_(
                                model.storage["article_refresh"].as_boolean().is_(True),
                                model.storage["input_url"].as_string() == Article.canonical_url,
                                model.storage["previous_image_url"]
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
        eligibility = and_(eligibility, or_(model.article_id.is_(None), current_article))
    return eligibility


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
    if DELIVERY_POLICY.expired(job, utcnow()):
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
        successor = request_analysis(session, job.article_id)
        if (
            job.steps
            and successor.id != job.id
            and successor.input_hash == job.input_hash
            and successor.status == "queued"
            and not successor.attempts
            and not successor.steps
        ):
            successor.steps = {
                name: dict(step)
                for name, step in job.steps.items()
                if isinstance(step, dict) and step.get("status") == "succeeded"
            }
        return successor
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
