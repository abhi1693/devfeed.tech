"""RQ analysis handler: short claims, bounded inference, revision-checked apply."""

import logging
import time
import uuid

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.ai_content import eligible_article
from devfeed_core.analysis import (
    AnalysisResult,
    analysis_candidates,
    analysis_catalog_current,
    analysis_output_schema,
    analysis_prompt,
    apply_analysis,
    catalog,
    fail_analysis,
    refresh_superseded_analysis,
    snapshot_hash,
    source_snapshot,
    validate_evidence,
)
from devfeed_core.analysis_wire import compact_request, restore_identities
from devfeed_core.article_jobs import approved_sources
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.inference_validation import feedback_prompt, validation_feedback
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
from devfeed_core.logging import log_context
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleContent, utcnow
from pydantic import ValidationError
from sqlalchemy import select

from devfeed_aggregator.analysis_telemetry import record_attempt
from devfeed_aggregator.codex_client import AnalysisError, CodexClient

logger = logging.getLogger(__name__)


def analyze_article(job_id: str) -> None:
    with job_log_context("analysis", job_id):
        try:
            _analyze(uuid.UUID(job_id))
        except Exception:
            logger.exception("article_analysis_runtime_failed")
            raise


def _analyze(identifier):
    settings, factory = get_settings(), session_factory()
    with factory.begin() as session:
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.id == identifier).with_for_update()
        )
        if job is None or job.status != "queued" or job.available_at > utcnow():
            logger.debug("article_analysis_not_claimed")
            return
        if not settings.ai_enabled:
            fail_analysis(job, "ai_not_configured")
            logger.warning("article_analysis_failed", extra={"reason": "ai_not_configured"})
            return
        if not approved_sources(session, job.article_id, lock=True):
            finish_job(job, "unapproved", utcnow())
            logger.info("article_analysis_skipped", extra={"reason": "unapproved"})
            return
        article = session.scalar(
            select(Article).where(Article.id == job.article_id).with_for_update(of=Article)
        )
        if article is None:
            finish_job(job, "superseded", utcnow())
            logger.info("article_analysis_skipped", extra={"reason": "superseded"})
            return
        if not eligible_article(article):
            finish_job(job, "content_date_deferred", utcnow())
            logger.info("article_analysis_skipped", extra={"reason": "content_date_deferred"})
            return
        current = source_snapshot(article, session.get(ArticleContent, article.id))
        if (
            snapshot_hash(current) != job.input_hash
            or article.editorial_revision != job.editorial_revision
            or article.review_status == "rejected"
        ):
            finish_job(job, "superseded", utcnow())
            refresh_superseded_analysis(session, article, job)
            logger.info("article_analysis_skipped", extra={"reason": "superseded"})
            return
        token = start_job(job, utcnow(), 300)
        # Old queued jobs run against the current catalog and output contract.
        from devfeed_core.analysis import PROMPT_VERSION

        job.prompt_version = PROMPT_VERSION
        job.model = settings.codex_model
        snapshot, article_id = job.input_snapshot, job.article_id
        attempt = job.attempts
    with log_context(article_id=article_id, attempt=attempt):
        _analyze_claimed(settings, factory, identifier, token, snapshot, article_id)


def _analyze_claimed(settings, factory, identifier, token, snapshot, article_id):
    logger.info("article_analysis_started")
    started, client, attempt = time.perf_counter(), None, None
    try:
        # No transaction remains open while waiting for Codex.
        with factory() as session:
            taxonomy = analysis_candidates(catalog(session), snapshot)
        with factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, identifier, token)
            if job is None:
                logger.warning("article_analysis_lease_lost")
                return
            job.catalog_snapshot = taxonomy
            job.catalog_hash = snapshot_hash(taxonomy)
            attempt = job.attempts
            feedback = (job.result or {}).get("validation_feedback")
            compact = getattr(settings, "ai_compact_article_prompts", False)
            job.usage = {
                **(job.usage or {}),
                "prompt_format": "compact-json-v1" if compact else "original",
            }
            reason = job.usage.get("requested_reason", "queued_analysis")
        client = CodexClient(settings)
        client.operation = "article_analysis"
        client.job_id = identifier
        client.attempt = attempt
        client.reason = reason
        client.quality_failure = bool(feedback) and attempt == 2
        if compact:
            prompt, schema, identities = compact_request(snapshot, taxonomy)
        else:
            prompt, schema = analysis_prompt(snapshot, taxonomy), analysis_output_schema(taxonomy)
        output = client.complete(prompt + feedback_prompt(feedback), schema)
        if compact:
            output = restore_identities(output, identities)
        result = AnalysisResult.model_validate(output)
        validate_evidence(result, snapshot, taxonomy)
        with factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, identifier, token)
            if job is None:
                logger.warning("article_analysis_lease_lost")
                return
            job.result = result.model_dump(mode="json")
            job.model = getattr(client, "model", settings.codex_model)
            # Lock source review before Article, matching ingestion lock order.
            if not approved_sources(session, article_id, lock=True):
                finish_job(job, "unapproved", utcnow())
            else:
                article = session.scalar(
                    select(Article).where(Article.id == article_id).with_for_update(of=Article)
                )
                if article is None:
                    finish_job(job, "superseded", utcnow())
                elif not eligible_article(article):
                    finish_job(job, "content_date_deferred", utcnow())
                else:
                    # Deleted catalog IDs or changed topic status cannot bypass
                    # application validation between inference and application.
                    from devfeed_core.topics import lock_topics

                    lock_topics(session)
                    current_catalog = catalog(session)
                    if not analysis_catalog_current(job, current_catalog, snapshot):
                        finish_job(job, "superseded", utcnow())
                    else:
                        validate_evidence(result, snapshot, current_catalog)
                        apply_analysis(session, article, job, result)
                        if settings.full_automation and job.outcome in {
                            "applied",
                            "insufficient_evidence",
                        }:
                            from devfeed_core.article_automation import propose_source_topics

                            propose_source_topics(session, article)
                        if job.outcome == "applied":
                            from devfeed_core.publication_policy import apply_publication_policy

                            apply_publication_policy(
                                session, article, job, taxonomy=current_catalog
                            )
                    refresh_superseded_analysis(session, article, job)
            outcome = job.outcome
        logger.info("article_analysis_completed", extra={"outcome": outcome})
    except Exception as exc:
        feedback = validation_feedback(exc)
        reason = (
            str(exc)
            if isinstance(exc, AnalysisError)
            else "invalid_analysis_result"
            if isinstance(exc, (ValueError, ValidationError))
            else "analysis_dependency_failure"
        )
        cooldown = safe_pause(getattr(exc, "retry_after", 0)) if reason in CAPACITY_ERRORS else 0
        with factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, identifier, token)
            if job is not None:
                if feedback is not None:
                    job.result = {**(job.result or {}), "validation_feedback": feedback}
                fail_analysis(job, reason, retry_after=cooldown)
                if (
                    getattr(settings, "ai_tiered_routing_enabled", False)
                    and feedback
                    and (attempt or 0) >= 2
                ):
                    from devfeed_core.job_lifecycle import fail_or_retry

                    fail_or_retry(job, reason, utcnow(), retryable=False)
        logger.warning(
            "article_analysis_failed",
            extra={
                "reason": reason,
                "validation_code": feedback["code"] if feedback else None,
                "validation_fields": feedback["fields"] if feedback else [],
            },
            exc_info=True,
        )
    finally:
        if client is not None and attempt is not None:
            record_attempt(
                factory, ArticleAnalysisJob, identifier, client, started, attempt=attempt
            )
