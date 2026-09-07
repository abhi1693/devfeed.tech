"""Domain events become durable inbox deliveries in the same database transaction.

No network calls or log scraping here. Rollbacks cannot publish notifications.
The notification service lives behind HTTP; it never shares application tables.
"""

import hashlib
import json
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, field_validator
from sqlalchemy import event, inspect
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from devfeed_core.config import get_settings
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    NotificationDelivery,
    SourceEnrichmentJob,
    TopicAnalysisJob,
)

Audience = Literal["admin", "user"]
Severity = Literal["info", "success", "warning", "error"]


def notification_subscriber_id(
    *, audience: Audience, issuer: str, subject: str, organization_id: str = ""
) -> str:
    """Same identity contract for present admin and future user API adapters."""
    identity = json.dumps([issuer, organization_id, subject], separators=(",", ":"))
    return audience + "_" + hashlib.sha256(identity.encode()).hexdigest()


class NotificationMessage(BaseModel):
    """Reusable contract for job events and future announcements; plain text only."""

    category: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    action_url: Annotated[str, StringConstraints(pattern=r"^/[a-zA-Z0-9/_-]*$", max_length=500)]
    severity: Severity = "info"

    @field_validator("action_url")
    @classmethod
    def local_action(cls, value):
        if value.startswith("//"):
            raise ValueError("Notification action must stay inside the receiving app")
        return value


def enqueue_notification(
    session: Session,
    event_key: str,
    notification: NotificationMessage,
    *,
    audience: Audience,
    subscriber_id: str | None = None,
) -> uuid.UUID | None:
    """Call inside the transaction that makes the event true. No immediate dispatch.

    Reusing a stable event key is a no-op. Intentionally disabled integrations do
    not accumulate a historical backlog. Chimely outages while enabled do.
    """
    if not get_settings().notifications_enabled:
        return None
    return _insert(
        session.connection(),
        event_key,
        notification,
        audience=audience,
        subscriber_id=subscriber_id,
    )


def _insert(
    connection: Connection,
    event_key: str,
    notification: NotificationMessage,
    *,
    audience: Audience,
    subscriber_id: str | None = None,
):
    if not event_key or len(event_key) > 255:
        raise ValueError("Notification event key must be 1–255 characters")
    if audience not in {"admin", "user"}:
        raise ValueError("Unknown notification audience")
    if subscriber_id is not None and (
        not subscriber_id.startswith(audience + "_")
        or len(subscriber_id) > 128
        or not all(char.isascii() and (char.isalnum() or char in "_-") for char in subscriber_id)
    ):
        raise ValueError("Subscriber must belong to the notification audience")
    dedup_key = hashlib.sha256(
        json.dumps([audience, subscriber_id, event_key]).encode()
    ).hexdigest()
    return connection.scalar(
        insert(NotificationDelivery)
        .values(
            event_key=event_key,
            dedup_key=dedup_key,
            audience=audience,
            subscriber_id=subscriber_id,
            category=notification.category,
            payload=notification.model_dump(exclude={"category"}),
        )
        .on_conflict_do_nothing(index_elements=[NotificationDelivery.dedup_key])
        .returning(NotificationDelivery.id)
    )


PIPELINES = {
    IngestionJob: ("ingestion", "Feed ingestion", "jobs/ingestion"),
    ArticleEnrichmentJob: ("article-enrichment", "Article enrichment", "jobs/enrichment/articles"),
    ArticleImageJob: ("images", "Image lookup", "jobs/enrichment/images"),
    SourceEnrichmentJob: ("source-enrichment", "Source enrichment", "jobs/enrichment/sources"),
    ArticleAnalysisJob: ("analysis", "Article analysis", "jobs/analysis/articles"),
    TopicAnalysisJob: ("topic-analysis", "Topic research", "jobs/analysis/topics"),
}


def job_notification(job) -> NotificationMessage | None:
    kind, label, resource = PIPELINES[type(job)]
    attempt = job.attempts or 0
    severity: Severity
    if job.status == "failed":
        title, severity = f"{label} failed", "error"
        body = (
            f"Run {str(job.id)[:8]} failed after {attempt} attempt(s). "
            "Open the run for details and runtime logs."
        )
    elif job.status == "queued" and attempt and job.error:
        title, severity = f"{label} will retry", "warning"
        body = (
            f"Run {str(job.id)[:8]} did not complete on attempt {attempt}. "
            "A retry is scheduled; open the run for details."
        )
    elif job.status == "succeeded":
        # Keep scheduled no-ops quiet. First-time useful results and recovered
        # runs remain visible, without turning every log line into an inbox item.
        changed = (
            (getattr(job, "articles_created", 0) or 0) > 0
            or bool(getattr(job, "changed_fields", None))
            or getattr(job, "outcome", None) in {"found", "ready", "applied", "enriched"}
        )
        if not changed and attempt <= 1:
            return None
        title, severity = f"{label} completed", "success"
        body = f"Run {str(job.id)[:8]} completed on attempt {attempt}."
        if isinstance(job, IngestionJob):
            body += (
                f" {job.articles_created or 0} new articles; "
                f"{job.entries_skipped or 0} entries skipped."
            )
        elif getattr(job, "changed_fields", None):
            body += f" Updated {len(job.changed_fields)} metadata field(s)."
        body += " Open the run for details and runtime logs."
    else:
        return None
    return NotificationMessage(
        category=f"jobs.{kind}",
        title=title,
        body=body,
        action_url=f"/{resource}/{job.id}",
        severity=severity,
    )


def record_job_transition(mapper, connection, job):
    if not get_settings().notifications_enabled:
        return
    # after_update also runs for dirty objects with no net change. Dispatch
    # timestamps, duplicate deliveries and lease renewals must never notify.
    if not inspect(job).attrs.status.history.has_changes():
        return
    notification = job_notification(job)
    if notification is not None:
        kind = PIPELINES[type(job)][0]
        _insert(
            connection,
            f"job:{kind}:{job.id}:{job.attempts}:{job.status}",
            notification,
            audience="admin",
        )


for model in PIPELINES:
    event.listen(model, "after_insert", record_job_transition)
    event.listen(model, "after_update", record_job_transition)
