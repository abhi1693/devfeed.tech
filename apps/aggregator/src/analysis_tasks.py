"""RQ analysis handler: short claims, bounded inference, revision-checked apply."""

import logging
import time
import uuid

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.analysis import (
    AnalysisResult,
    analysis_candidates,
    analysis_prompt,
    apply_analysis,
    catalog,
    fail_analysis,
    refresh_superseded_analysis,
    snapshot_hash,
    source_snapshot,
    validate_evidence,
)
from devfeed_core.article_jobs import approved_sources
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
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
        client = CodexClient(settings)
        output = client.complete(
            analysis_prompt(snapshot, taxonomy), AnalysisResult.model_json_schema()
        )
        result = AnalysisResult.model_validate(output)
        validate_evidence(result, snapshot, taxonomy)
        with factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, identifier, token)
            if job is None:
                logger.warning("article_analysis_lease_lost")
                return
            job.result = result.model_dump(mode="json")
            # Lock source review before Article, matching ingestion lock order.
            if not approved_sources(session, article_id, lock=True):
                finish_job(job, "unapproved", utcnow())
            else:
                article = session.scalar(
                    select(Article).where(Article.id == article_id).with_for_update(of=Article)
                )
                if article is None:
                    finish_job(job, "superseded", utcnow())
                else:
                    # Deleted catalog IDs or changed topic status cannot bypass
                    # application validation between inference and application.
                    from devfeed_core.topics import lock_topics

                    lock_topics(session)
                    current_catalog = catalog(session)
                    if (
                        snapshot_hash(analysis_candidates(current_catalog, snapshot))
                        != job.catalog_hash
                    ):
                        finish_job(job, "superseded", utcnow())
                    else:
                        validate_evidence(result, snapshot, current_catalog)
                        apply_analysis(session, article, job, result)
                        if job.outcome == "applied":
                            from devfeed_core.publication_policy import apply_publication_policy

                            apply_publication_policy(
                                session, article, job, taxonomy=current_catalog
                            )
                    refresh_superseded_analysis(session, article, job)
            outcome = job.outcome
        logger.info("article_analysis_completed", extra={"outcome": outcome})
    except Exception as exc:
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
                fail_analysis(job, reason, retry_after=cooldown)
        logger.warning("article_analysis_failed", extra={"reason": reason}, exc_info=True)
    finally:
        if client is not None and attempt is not None:
            record_attempt(
                factory, ArticleAnalysisJob, identifier, client, started, attempt=attempt
            )
