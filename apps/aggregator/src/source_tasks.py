"""Background source profile lookup, separate from RSS article ingestion."""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, fetch_feed, fetch_source_page
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.inference_usage import inference_context
from devfeed_core.inference_validation import CODES, validation_feedback
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
from devfeed_core.json_types import JsonRecord
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.models import Source, SourceEnrichmentJob, utcnow
from devfeed_core.source_enrichment import claim_enrichment, fill_profile, source_identity
from devfeed_core.source_inspection import complete_website_profile
from devfeed_core.source_profiles import PROFILE_FIELDS
from devfeed_core.source_relevance import feed_sample
from devfeed_core.source_types import SourceType
from devfeed_core.worker_queues import ROUTABLE_QUEUES, configured_queue
from rq import get_current_job
from rq.job import Job
from sqlalchemy import select

from devfeed_aggregator.codex_client import AnalysisError
from devfeed_aggregator.solver_jobs import defer_to_solver
from devfeed_aggregator.source_relevance import assess_source

logger = logging.getLogger(__name__)


def lookup_profile(
    url: str, source_type: SourceType, existing: dict[str, str | None]
) -> tuple[JsonRecord, FeedError | None]:
    try:
        result = fetch_feed(url)
    except FeedError as exc:
        exc.resource = "Feed"
        raise
    if result.status != 200:
        raise FeedError("Source metadata needs a complete feed", reason="missing_body")
    parsed = parse_feed(result.body, result.final_url, utcnow(), source_type=source_type)
    values = asdict(parsed.profile)
    sample = feed_sample(parsed)
    website = existing["website_url"] or values["website_url"]
    if not website:
        parts = urlsplit(result.final_url)
        website = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
    error = None
    if any(existing[field] is None and not values[field] for field in PROFILE_FIELDS):
        try:
            values = complete_website_profile(
                values, website, fetch=fetch_source_page, fallback_to_root=True
            )
        except FeedError as exc:
            exc.resource = "Source website"
            error = exc
    return {**values, "_sample": sample}, error


def enrich_source(job_id: str) -> None:
    with job_log_context("source-enrichment", job_id):
        try:
            _enrich_source(uuid.UUID(job_id))
        except Exception:
            logger.exception("source_enrichment_runtime_failed")
            raise


def _relevance_delivery(delivery: Job) -> bool:
    allowed = {"source-analysis", "solver"}
    logical = (getattr(delivery, "meta", None) or {}).get("devfeed_queue")
    if logical is not None:
        return isinstance(logical, str) and logical in allowed
    # A dedicated configured destination establishes execution capability. A
    # shared priority queue cannot establish the kind of an unlabelled delivery.
    owners = {name for name in ROUTABLE_QUEUES if configured_queue(name) == delivery.origin}
    return bool(owners) and owners <= allowed


def _enrich_source(identifier: uuid.UUID) -> None:
    started = time.perf_counter()
    factory = session_factory()
    job: SourceEnrichmentJob | None
    source: Source | None
    with factory.begin() as session:
        claimed = claim_enrichment(session, identifier)
        if claimed is None:
            logger.debug("source_enrichment_not_claimed")
            return
        job, source = claimed
        delivery = get_current_job()
        if (
            job.operation == "relevance"
            and delivery is not None
            and not _relevance_delivery(delivery)
        ):
            # A source whose operation changed must be admitted through the
            # assessment lane before invoking Codex.
            job.status = "queued"
            job.available_at = utcnow()
            job.dispatched_at = None
            job.lease_token = job.lease_until = None
            job.attempts -= 1
            logger.info("source_relevance_rerouted", extra={"source_id": source.id})
            return
        source_id, token, attempt = source.id, job.lease_token, job.attempts
        url, source_type = source.feed_url, SourceType(source.source_type)
        operation, captured = job.operation, dict(job.result or {})
        original = {field: getattr(source, field) for field in PROFILE_FIELDS}
        previous_code = (job.error or "").removeprefix("Source relevance validation failed: ")
    assert token is not None  # claim_enrichment initializes ownership.
    with (
        log_context(source_id=source_id, attempt=attempt),
        inference_context(
            operation="source_relevance", job_id=identifier, attempt=attempt, reason="source_review"
        ),
    ):
        logger.info(
            "source_relevance_started" if operation == "relevance" else "source_enrichment_started",
            extra={"operation": operation},
        )
        candidates: JsonRecord = {}
        error: Exception | None = None
        assessment: JsonRecord | None = None
        stage = operation
        try:
            if url is None:
                raise TypeError("Source has no feed URL")
            if operation == "profile":
                candidates, error = lookup_profile(url, source_type, original)
            else:
                assessment = (
                    assess_source(
                        url,
                        source_type,
                        sample=captured.get("sample", []),
                        feedback={"code": previous_code, "fields": []},
                    )
                    if previous_code in CODES
                    else assess_source(url, source_type, sample=captured.get("sample", []))
                )
        except Exception as exc:
            error = exc
        feedback = (
            validation_feedback(error) if error is not None and stage == "relevance" else None
        )
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
            if captured.get("input") != source_identity(source):
                fail_or_retry(job, "Source input changed", utcnow(), retryable=False)
                session.flush()
                if source.approval_status == "pending" or (
                    source.approval_status == "approved"
                    and not source.enabled
                    and source.processing_request
                ):
                    from devfeed_core.source_lifecycle import reconcile_source_processing

                    reconcile_source_processing(session, source, automatic=True)
                return
            changed = fill_profile(source, candidates, original) if operation == "profile" else []
            job.changed_fields = sorted(set(job.changed_fields or []) | set(changed))
            if operation == "profile":
                sample = candidates.get("_sample", [])
                job.result = {
                    **captured,
                    "input": source_identity(source),
                    "profile": {field: candidates.get(field) for field in PROFILE_FIELDS},
                    "sample": sample,
                    "sample_hash": hashlib.sha256(
                        json.dumps(sample, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest(),
                }
            elif assessment is not None:
                assessment = {
                    **assessment,
                    "profile_job_id": captured.get("profile_job_id"),
                    "sample_hash": captured.get("sample_hash"),
                }
                source.relevance_assessment = assessment
                job.result = {**captured, "output": assessment}
            if error is not None:
                transport = error if isinstance(error, FeedError) else None
                analysis = error if isinstance(error, AnalysisError) else None
                message = (
                    f"Source relevance validation failed: {feedback['code']}"
                    if feedback
                    else f"Source relevance analysis failed: {analysis}"
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
                            safe_pause(analysis.retry_after, reason=str(analysis))
                            if analysis and str(analysis) in CAPACITY_ERRORS
                            else getattr(error, "retry_after", 0)
                        ),
                    )
                if operation == "profile":
                    source.metadata_error = message
            else:
                job.status = "succeeded"
                job.finished_at = utcnow()
                job.lease_token = None
                job.lease_until = None
                job.error = None
                if operation == "profile":
                    source.metadata_error = None
                    source.metadata_enriched_at = job.finished_at
                session.flush()
                from devfeed_core.source_lifecycle import reconcile_source_processing

                reconcile_source_processing(session, source, automatic=True)
            fields = {
                "operation": operation,
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
                    "validation_code": feedback["code"] if feedback else None,
                    "validation_fields": feedback["fields"] if feedback else [],
                    "upstream_status": error.status if isinstance(error, FeedError) else None,
                },
                exc_info=(type(error), error, error.__traceback__)
                if not isinstance(error, (FeedError, AnalysisError))
                else None,
            )
        else:
            logger.info(
                "source_relevance_completed"
                if operation == "relevance"
                else "source_enrichment_completed",
                extra=fields,
            )
