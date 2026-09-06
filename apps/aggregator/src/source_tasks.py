"""Background source profile lookup, separate from RSS article ingestion."""

import logging
import time
import uuid
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, fetch_feed, fetch_page
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.models import Source, SourceEnrichmentJob, utcnow
from devfeed_core.source_enrichment import claim_enrichment, fail_enrichment, fill_profile
from devfeed_core.source_profiles import PROFILE_FIELDS, website_profile
from sqlalchemy import select

logger = logging.getLogger(__name__)


def lookup_profile(url, source_type, existing):
    result = fetch_feed(url)
    if result.status != 200:
        raise FeedError("Source metadata needs a complete feed", reason="missing_body")
    parsed = parse_feed(result.body, result.final_url, utcnow(), source_type=source_type)
    values = asdict(parsed.profile)
    website = existing["website_url"] or values["website_url"]
    if not website:
        parts = urlsplit(result.final_url)
        website = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
    error = None
    if any(existing[field] is None and not values[field] for field in PROFILE_FIELDS):
        try:
            page = website_profile(fetch_page(website))
            for field in PROFILE_FIELDS:
                values[field] = values[field] or getattr(page, field)
        except FeedError as exc:
            error = exc
    return values, error


def enrich_source(job_id: str) -> None:
    with log_context(service="worker", job_id=job_id):
        try:
            _enrich_source(uuid.UUID(job_id))
        except Exception:
            logger.exception("source_enrichment_runtime_failed")
            raise


def _enrich_source(identifier):
    started = time.perf_counter()
    factory = session_factory()
    with factory.begin() as session:
        claimed = claim_enrichment(session, identifier)
        if claimed is None:
            return
        job, source = claimed
        source_id, token, attempt = source.id, job.lease_token, job.attempts
        url, source_type = source.feed_url, source.source_type
        original = {field: getattr(source, field) for field in PROFILE_FIELDS}
    with log_context(source_id=source_id, attempt=attempt):
        logger.info("source_enrichment_started")
        candidates, error = {}, None
        try:
            candidates, error = lookup_profile(url, source_type, original)
        except Exception as exc:
            error = exc
        with factory.begin() as session:
            job = session.scalar(
                select(SourceEnrichmentJob)
                .where(SourceEnrichmentJob.id == identifier)
                .with_for_update()
            )
            if job is None or job.status != "running" or job.lease_token != token:
                logger.warning("source_enrichment_lease_lost")
                return
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if source is None:
                return
            if source.approval_status == "rejected":
                fail_enrichment(job, "Source was rejected during enrichment", retryable=False)
                return
            changed = fill_profile(source, candidates, original)
            job.changed_fields = sorted(set(job.changed_fields or []) | set(changed))
            if error is not None:
                transport = error if isinstance(error, FeedError) else None
                message = (
                    f"Source enrichment failed: {transport.reason}"
                    if transport
                    else f"Source enrichment error: {type(error).__name__}"
                )
                fail_enrichment(
                    job,
                    message,
                    retryable=transport.retryable if transport else True,
                    retry_after=transport.retry_after if transport else 0,
                )
                source.metadata_error = message
            else:
                job.status = "succeeded"
                job.finished_at = utcnow()
                job.lease_token = None
                job.lease_until = None
                job.error = None
                source.metadata_error = None
                source.metadata_enriched_at = job.finished_at
            fields = {
                "changed_fields": changed,
                "job_status": job.status,
                "available_at": job.available_at,
                "duration_ms": elapsed_ms(started),
            }
        if error:
            logger.log(
                logging.WARNING if isinstance(error, FeedError) else logging.ERROR,
                "source_enrichment_failed",
                extra={
                    **fields,
                    "reason": error.reason if isinstance(error, FeedError) else "unexpected_error",
                    "error_type": type(error).__name__,
                    "upstream_status": error.status if isinstance(error, FeedError) else None,
                },
                exc_info=(type(error), error, error.__traceback__)
                if not isinstance(error, FeedError)
                else None,
            )
        else:
            logger.info("source_enrichment_completed", extra=fields)
