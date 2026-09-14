"""Source metadata outbox operations. No network work occurs inside transactions."""

from sqlalchemy import select

from devfeed_core.job_lifecycle import fail_or_retry, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.models import Source, SourceEnrichmentJob, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_profiles import PROFILE_FIELDS


def request_source_review(session, source):
    """All entry points share discovery followed by source enrichment/review."""
    if source.feed_url:
        return request_enrichment(session, source.id)
    from devfeed_core.discovery import enqueue
    from devfeed_core.discovery_import import identity_url
    from devfeed_core.models import SourceCandidate

    if source.approval_status != "pending" or not source.website_url:
        raise OperationConflict("A pending source with a website is required for discovery")
    candidate = session.scalar(
        select(SourceCandidate).where(SourceCandidate.source_id == source.id)
    )
    if candidate is None:
        candidate = SourceCandidate(
            id=source.id,
            name=source.name,
            identity_url=identity_url(source.website_url),
            source_id=source.id,
        )
        session.add(candidate)
        session.flush()
    enqueue(session, candidate.id, background=True)
    return None


def request_enrichment(session, source_id, *, supersede=False):
    source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise RecordNotFound("Source not found")
    if source.approval_status == "rejected":
        raise OperationConflict("Rejected sources cannot be enriched")
    if not source.feed_url:
        raise OperationConflict("Feed discovery must complete before enrichment")
    active = session.scalar(
        select(SourceEnrichmentJob).where(
            SourceEnrichmentJob.source_id == source_id,
            SourceEnrichmentJob.status.in_(["queued", "running"]),
        )
    )
    if active:
        if not supersede:
            return active
        fail_or_retry(active, "Source feed changed", utcnow(), retryable=False)
        session.flush()
    job = SourceEnrichmentJob(source_id=source_id)
    session.add(job)
    session.flush()
    return job


def claim_enrichment(session, job_id):
    # Source-first locking matches deletion and prevents a worker/deletion
    # deadlock. The subquery only reads the delivery's immutable source identity.
    source = session.scalar(
        select(Source)
        .where(
            Source.id
            == select(SourceEnrichmentJob.source_id)
            .where(SourceEnrichmentJob.id == job_id)
            .scalar_subquery()
        )
        .with_for_update()
    )
    if source is None:
        return None
    # Still wait out publication before checking the durable job state.
    job = session.scalar(
        select(SourceEnrichmentJob).where(SourceEnrichmentJob.id == job_id).with_for_update()
    )
    if job is None or job.status != "queued" or job.available_at > utcnow():
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
