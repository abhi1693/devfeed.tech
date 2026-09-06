import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_core.models import IngestionJob, Source, utcnow

MAX_ATTEMPTS = 3
JOB_TIMEOUT_SECONDS = 180
LEASE_SECONDS = 300
REDISPATCH_SECONDS = 300


def request_ingestion(session: Session, source: Source) -> IngestionJob:
    """Caller must lock the source row. The partial unique index is a second guard."""
    if source.approval_status != "approved":
        raise ValueError("Only approved sources can be ingested")
    existing = session.scalar(
        select(IngestionJob).where(
            IngestionJob.source_id == source.id, IngestionJob.status.in_(["queued", "running"])
        )
    )
    if existing:
        return existing
    job = IngestionJob(source_id=source.id)
    session.add(job)
    source.next_fetch_at = utcnow() + timedelta(seconds=source.poll_interval_seconds)
    session.flush()
    return job


def fail_job(
    session: Session, job: IngestionJob, error: str, *, retryable: bool = True, retry_after: int = 0
) -> None:
    source = session.scalar(select(Source).where(Source.id == job.source_id).with_for_update())
    assert source is not None
    if source.approval_status != "approved":
        cancel_unapproved_job(job)
        return
    now = utcnow()
    job.error = error[:1000]
    job.lease_token = None
    job.lease_until = None
    job.dispatched_at = None
    source.last_error = job.error
    if (
        retryable
        and job.attempts < MAX_ATTEMPTS
        and source.enabled
        and source.approval_status == "approved"
    ):
        job.status = "queued"
        job.available_at = now + timedelta(seconds=max(30 * 2 ** (job.attempts - 1), retry_after))
    else:
        job.status = "failed"
        job.finished_at = now
        source.consecutive_failures += 1
        delay = min(86400, source.poll_interval_seconds * 2 ** min(source.consecutive_failures, 8))
        source.next_fetch_at = now + timedelta(seconds=max(delay, retry_after))


def claim_job(session: Session, job_id: uuid.UUID) -> tuple[IngestionJob, Source] | None:
    # A targeted delivery must wait for the dispatcher to finish publishing and
    # commit its row lock. SKIP LOCKED would acknowledge and lose that delivery.
    job = session.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
    now = utcnow()
    if job is None or job.status != "queued" or job.available_at > now:
        return None
    source = session.scalar(select(Source).where(Source.id == job.source_id).with_for_update())
    assert source is not None
    if source.approval_status != "approved":
        cancel_unapproved_job(job)
        return None
    if not source.enabled:
        fail_job(session, job, "Source is disabled", retryable=False)
        return None
    job.status = "running"
    job.attempts += 1
    job.lease_token = uuid.uuid4()
    job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    source.last_attempt_at = now
    return job, source


def cancel_unapproved_job(job: IngestionJob) -> None:
    """A review decision is not an upstream feed failure; do not back off its source."""
    job.status = "failed"
    job.error = "Source is not approved for ingestion"
    job.finished_at = utcnow()
    job.lease_token = None
    job.lease_until = None
