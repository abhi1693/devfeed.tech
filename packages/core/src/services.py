"""Core operations. Callers own the transaction; all writes are flushed, not committed."""

import uuid
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.feeds.validation import validate_feed
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import IngestionJob, Source, SourceReview, Tag, Topic, utcnow
from devfeed_core.schemas import (
    SourceCreate,
    SourceDecision,
    SourcePatch,
    TagPatch,
    TagWrite,
)
from devfeed_core.source_profiles import PROFILE_FIELDS
from devfeed_core.source_types import SourceType


class RecordNotFound(ValueError):
    pass


class OperationConflict(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSource:
    name: str
    feed_url: str
    source_type: SourceType
    enabled: bool
    poll_interval_seconds: int
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    image_url: str | None = None
    language: str | None = None
    submitted_by: dict | None = None


def validate_source(body: SourceCreate) -> ValidatedSource:
    """Perform network preflight before opening the source-write transaction."""
    # Snapshot validated inputs before I/O; callers cannot change what gets saved
    # by mutating the request while its preflight is in progress.
    values = body.model_dump()
    feed = validate_feed(values["feed_url"], source_type=values["source_type"])
    if values["name"] is None:
        hostname = urlsplit(values["feed_url"]).hostname
        assert hostname is not None  # SourceCreate has already validated the URL.
        values["name"] = feed.title or hostname[:200]
    for field in PROFILE_FIELDS:
        if values[field] is None:
            values[field] = getattr(feed.profile, field)
    return ValidatedSource(**values)


def create_source(session: Session, body: ValidatedSource) -> Source:
    source = Source(**asdict(body), approval_status="pending", submission_channel="api")
    session.add(source)
    session.flush()
    return source


def submit_source(
    session: Session, body: ValidatedSource
) -> tuple[Source, bool, IngestionJob | None]:
    """Idempotent submission: preserve existing source settings and coalesce active jobs."""
    inserted = session.scalar(
        insert(Source)
        .values(
            **asdict(body),
            approval_status="approved",
            submission_channel="cli",
            reviewed_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=[Source.feed_url])
        .returning(Source.id)
    )
    source = session.scalar(
        select(Source).where(Source.feed_url == body.feed_url).with_for_update()
    )
    assert source is not None
    if source.source_type != body.source_type:
        raise OperationConflict("This feed already exists with a different source type")
    if inserted is not None:
        session.add(
            SourceReview(source_id=source.id, decision="approved", note="Trusted CLI submission")
        )
        if source.enabled:
            from devfeed_core.source_enrichment import request_enrichment

            request_enrichment(session, source.id)
    job = (
        request_ingestion(session, source)
        if source.enabled and source.approval_status == "approved"
        else None
    )
    return source, inserted is not None, job


def update_source(session: Session, source_id: uuid.UUID, body: SourcePatch) -> Source:
    source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise RecordNotFound("Source not found")
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(source, key, value)
    if changes.get("enabled") is True:
        source.next_fetch_at = utcnow()
    session.flush()
    return source


def fetch_source(session: Session, source_id: uuid.UUID) -> IngestionJob:
    source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise RecordNotFound("Source not found")
    if source.approval_status != "approved":
        raise OperationConflict("Approve the source before requesting ingestion")
    if not source.enabled:
        raise OperationConflict("Enable the source before requesting ingestion")
    return request_ingestion(session, source)


def review_source(
    session: Session,
    source_id: uuid.UUID,
    body: SourceDecision,
    *,
    enable_on_approval: bool = True,
) -> Source:
    from devfeed_core.source_enrichment import request_enrichment

    if body.decision == "rejected" and not body.note:
        raise OperationConflict("A reason is required when rejecting a source")
    source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise RecordNotFound("Source not found")
    if source.approval_status == body.decision:
        return source  # Repeating a decision does not invent new history/jobs.
    source.approval_status = body.decision
    source.reviewed_at = utcnow()
    source.reviewed_by = body.actor
    source.review_note = body.note
    source.enabled = body.decision == "approved" and enable_on_approval
    session.add(
        SourceReview(source_id=source.id, decision=body.decision, actor=body.actor, note=body.note)
    )
    if source.enabled:
        source.next_fetch_at = utcnow()
        request_ingestion(session, source)
        request_enrichment(session, source.id)
    session.flush()
    return source


def retry_job(session: Session, job_id: uuid.UUID) -> IngestionJob:
    previous = session.get(IngestionJob, job_id)
    if previous is None:
        raise RecordNotFound("Job not found")
    if previous.status != "failed":
        raise OperationConflict("Only failed jobs can be retried explicitly")
    # Retain the failed run for diagnosis; fetch_source coalesces any newer active run.
    return fetch_source(session, previous.source_id)


def prepare_immediate_dispatch(session: Session, job_id: uuid.UUID) -> IngestionJob:
    """Persist an explicit override of queued delays, without stealing a worker lease.

    Call in its own transaction, without a source-row lock: workers acquire the
    job row before updating a source. Commit before attempting broker publication.
    """
    job = session.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
    if job is None:
        raise RecordNotFound("Job not found")
    if job.status != "queued":
        raise OperationConflict(
            "Only queued jobs can be dispatched immediately; running jobs keep their lease. "
            "Use jobs retry for a failed job or sources fetch for a completed run."
        )
    source = session.get(Source, job.source_id)
    if source is None:
        raise RecordNotFound("Source not found")
    if source.approval_status != "approved":
        raise OperationConflict("Approve the source before dispatching ingestion")
    if not source.enabled:
        raise OperationConflict("Enable the source before dispatching ingestion")
    job.available_at = utcnow()
    job.dispatched_at = None
    session.flush()
    return job


def validate_topic(session: Session, topic_id: uuid.UUID | None) -> None:
    if topic_id is not None:
        topic = session.get(Topic, topic_id)
        if topic is None or topic.status != "active":
            raise RecordNotFound("Active topic not found")


def create_tag(session: Session, body: TagWrite) -> Tag:
    validate_topic(session, body.topic_id)
    tag = Tag(**body.model_dump())
    session.add(tag)
    session.flush()
    return tag


def update_tag(session: Session, tag_id: uuid.UUID, body: TagWrite | TagPatch) -> Tag:
    tag = session.scalar(select(Tag).where(Tag.id == tag_id).with_for_update(of=Tag))
    if tag is None:
        raise RecordNotFound("Tag not found")
    changes = body.model_dump(exclude_unset=isinstance(body, TagPatch))
    validate_topic(session, changes.get("topic_id", tag.topic_id))
    for key, value in changes.items():
        setattr(tag, key, value)
    session.flush()
    return tag
