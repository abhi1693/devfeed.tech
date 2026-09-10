"""Source metadata outbox operations. No network work occurs inside transactions."""

from sqlalchemy import select

from devfeed_core.job_lifecycle import fail_or_retry, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.models import Source, SourceEnrichmentJob, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_profiles import PROFILE_FIELDS


def request_enrichment(session, source_id):
    source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise RecordNotFound("Source not found")
    if source.approval_status == "rejected":
        raise OperationConflict("Rejected sources cannot be enriched")
    active = session.scalar(
        select(SourceEnrichmentJob).where(
            SourceEnrichmentJob.source_id == source_id,
            SourceEnrichmentJob.status.in_(["queued", "running"]),
        )
    )
    if active:
        return active
    job = SourceEnrichmentJob(source_id=source_id)
    session.add(job)
    session.flush()
    return job


def claim_enrichment(session, job_id):
    # Wait out the dispatch transaction rather than consuming a still-locked job.
    job = session.scalar(
        select(SourceEnrichmentJob).where(SourceEnrichmentJob.id == job_id).with_for_update()
    )
    if job is None or job.status != "queued" or job.available_at > utcnow():
        return None
    source = session.scalar(select(Source).where(Source.id == job.source_id).with_for_update())
    if source is None:
        return None
    if source.approval_status == "rejected":
        fail_or_retry(job, "Source was rejected", utcnow(), retryable=False)
        return None
    start_job(job, utcnow(), LEASE_SECONDS)
    return job, source


def fill_profile(source, candidates: dict, original: dict) -> list[str]:
    changed: list[str] = []
    if source.website_url != original["website_url"]:
        return changed  # A manual website change invalidates fetched branding evidence.
    for field in PROFILE_FIELDS:
        # Only fill gaps that were already empty at claim and are still unchanged.
        # Never overwrite a manual edit, or the original submitter/review evidence.
        value = candidates.get(field)
        if value and original[field] is None and getattr(source, field) is None:
            setattr(source, field, value)
            changed.append(field)
    return changed


def prepare_enrichment_dispatch(session, job_id):
    job = session.scalar(
        select(SourceEnrichmentJob).where(SourceEnrichmentJob.id == job_id).with_for_update()
    )
    if job is None:
        raise RecordNotFound("Source enrichment job not found")
    if job.status != "queued":
        raise OperationConflict("Only queued enrichment jobs can be dispatched")
    source = session.get(Source, job.source_id)
    if source is None or source.approval_status == "rejected":
        raise OperationConflict("Rejected sources cannot be enriched")
    job.available_at = utcnow()
    job.dispatched_at = None
    session.flush()
    return job
