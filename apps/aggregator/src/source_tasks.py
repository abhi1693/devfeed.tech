"""Background source profile lookup, separate from RSS article ingestion."""

import logging
import time
import uuid
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, fetch_feed, fetch_source_page
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.models import Source, SourceEnrichmentJob, utcnow
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import review_source
from devfeed_core.source_enrichment import claim_enrichment, fill_profile
from devfeed_core.source_profiles import PROFILE_FIELDS, website_profile
from devfeed_core.source_relevance import requires_relevance
from rq import get_current_job
from sqlalchemy import select

from devfeed_aggregator.codex_client import AnalysisError
from devfeed_aggregator.solver_jobs import defer_to_solver
from devfeed_aggregator.source_relevance import assess_source

logger = logging.getLogger(__name__)


def lookup_profile(url, source_type, existing):
    try:
        result = fetch_feed(url)
    except FeedError as exc:
        exc.resource = "Feed"
        raise
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
            try:
                page_result = fetch_source_page(website)
            except FeedError as exc:
                # Some publishers incorrectly put their XML feed in the website
                # link. Try the site's root once, under the same transport guards.
                parts = urlsplit(website)
                root = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
                if exc.reason != "unsupported_content_type" or website == root:
                    raise
                page_result = fetch_source_page(root)
            page = website_profile(page_result)
            for field in PROFILE_FIELDS:
                values[field] = values[field] or getattr(page, field)
        except FeedError as exc:
            exc.resource = "Source website"
            error = exc
    return values, error


def enrich_source(job_id: str) -> None:
    with job_log_context("source-enrichment", job_id):
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
            logger.debug("source_enrichment_not_claimed")
            return
        job, source = claimed
        delivery = get_current_job()
        if (
            requires_relevance(source)
            and delivery is not None
            and delivery.origin not in {"analysis", "solver"}
        ):
            # A delivery queued before a mode change must not invoke Codex from
            # an ingestion worker. Return it to the outbox for current routing.
            job.status = "queued"
            job.available_at = utcnow()
            job.dispatched_at = None
            job.lease_token = job.lease_until = None
            job.attempts -= 1
            logger.info("source_relevance_rerouted", extra={"source_id": source.id})
            return
        source_id, token, attempt = source.id, job.lease_token, job.attempts
        url, source_type = source.feed_url, source.source_type
        assess = (
            bool((source.submitted_by or {}).get("user_id")) and source.approval_status == "pending"
        )
        original = {field: getattr(source, field) for field in PROFILE_FIELDS}
    with log_context(source_id=source_id, attempt=attempt):
        logger.info("source_enrichment_started")
        candidates, error, assessment = {}, None, None
        stage = "profile"
        try:
            candidates, error = lookup_profile(url, source_type, original)
            if assess and get_settings().full_automation and get_settings().ai_enabled:
                stage = "relevance"
                assessment = assess_source(url, source_type)
                if error is not None:
                    stage = "profile"
        except Exception as exc:
            error = exc
        with factory.begin() as session:
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if source is None:
                logger.info("source_enrichment_source_deleted")
                return
            job = owned_job(session, SourceEnrichmentJob, identifier, token)
            if job is None:
                logger.warning("source_enrichment_lease_lost")
                return
            if source.approval_status == "rejected":
                fail_or_retry(
                    job, "Source was rejected during enrichment", utcnow(), retryable=False
                )
                return
            if (
                assessment is not None
                and source.feed_url == url
                and source.source_type == source_type
            ):
                source.relevance_assessment = assessment
                if (
                    source.approval_status == "pending"
                    and get_settings().full_automation
                    and get_settings().ai_enabled
                    and assessment.get("approval_supported") is True
                ):
                    review_source(
                        session,
                        source.id,
                        SourceDecision(
                            decision="approved",
                            actor="devfeed:source-relevance",
                            note="Developer relevance verified from recent feed entries: "
                            + assessment["reason"][:850],
                        ),
                    )
            changed = fill_profile(source, candidates, original)
            job.changed_fields = sorted(set(job.changed_fields or []) | set(changed))
            if error is not None:
                transport = error if isinstance(error, FeedError) else None
                analysis = error if isinstance(error, AnalysisError) else None
                message = (
                    f"Source relevance analysis failed: {analysis}"
                    if analysis
                    else f"Source enrichment failed: {transport.reason}"
                    if transport
                    else f"Source enrichment error: {type(error).__name__}"
                )
                if transport and transport.reason != "response_too_large":
                    resource = transport.resource or "Publisher"
                    status = f" (HTTP {transport.status})" if transport.status is not None else ""
                    detail = {
                        "http_error": "rejected the request",
                        "browser_challenge": "requires browser verification",
                        "unsupported_content_type": "did not return an HTML page",
                        "solver_unavailable": "solver service is unavailable",
                        "solver_busy": "solver service is busy",
                    }.get(transport.reason)
                    if detail:
                        message += f". {resource} {detail}{status}."
                if transport and transport.reason == "response_too_large" and transport.limit_bytes:
                    resource = {
                        "DEVFEED_FEED_MAX_BYTES": "Feed",
                        "DEVFEED_SOURCE_PAGE_MAX_BYTES": "Source website",
                    }.get(transport.limit_setting or "", "Response")
                    message += (
                        f". {resource} exceeds the configured {transport.limit_bytes:,}-byte "
                        "download limit"
                    )
                    if transport.limit_setting:
                        message += f" ({transport.limit_setting})"
                    message += "."
                deferred = defer_to_solver(job, transport, message)
                if not deferred:
                    fail_or_retry(
                        job,
                        message,
                        utcnow(),
                        retryable=transport.retryable if transport else True,
                        retry_after=(
                            safe_pause(analysis.retry_after)
                            if analysis and str(analysis) in CAPACITY_ERRORS
                            else getattr(error, "retry_after", 0)
                        ),
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
                logging.WARNING if isinstance(error, (FeedError, AnalysisError)) else logging.ERROR,
                "source_enrichment_failed",
                extra={
                    **fields,
                    "reason": (
                        error.reason
                        if isinstance(error, FeedError)
                        else str(error)
                        if isinstance(error, AnalysisError)
                        else "invalid_analysis_result"
                        if stage == "relevance" and isinstance(error, ValueError)
                        else "unexpected_error"
                    ),
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "upstream_status": error.status if isinstance(error, FeedError) else None,
                },
                exc_info=(type(error), error, error.__traceback__)
                if not isinstance(error, (FeedError, AnalysisError))
                else None,
            )
        else:
            logger.info("source_enrichment_completed", extra=fields)
