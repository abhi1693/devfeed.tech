"""RQ analysis handler: short claims, bounded inference, revision-checked apply."""

import logging
import uuid
from datetime import timedelta

from devfeed_core.analysis import (
    AnalysisResult,
    analysis_prompt,
    apply_analysis,
    catalog,
    fail_analysis,
    finish_analysis,
    refresh_superseded_analysis,
    snapshot_hash,
    source_snapshot,
    validate_evidence,
)
from devfeed_core.article_jobs import approved_sources
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.job_logs import job_log_context
from devfeed_core.logging import log_context
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleContent, utcnow
from pydantic import ValidationError
from sqlalchemy import select

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
            fail_analysis(job, "ai_not_configured", retryable=False)
            logger.warning("article_analysis_failed", extra={"reason": "ai_not_configured"})
            return
        if not approved_sources(session, job.article_id, lock=True):
            finish_analysis(job, "unapproved")
            logger.info("article_analysis_skipped", extra={"reason": "unapproved"})
            return
        article = session.scalar(
            select(Article).where(Article.id == job.article_id).with_for_update(of=Article)
        )
        if article is None:
            finish_analysis(job, "superseded")
            logger.info("article_analysis_skipped", extra={"reason": "superseded"})
            return
        current = source_snapshot(article, session.get(ArticleContent, article.id))
        if (
            snapshot_hash(current) != job.input_hash
            or article.editorial_revision != job.editorial_revision
            or article.review_status == "rejected"
        ):
            finish_analysis(job, "superseded")
            refresh_superseded_analysis(session, article, job)
            logger.info("article_analysis_skipped", extra={"reason": "superseded"})
            return
        job.status, job.attempts = "running", job.attempts + 1
        job.lease_token = token = uuid.uuid4()
        job.lease_until = utcnow() + timedelta(seconds=300)
        job.model = settings.codex_model
        snapshot, article_id = job.input_snapshot, job.article_id
        attempt = job.attempts
    with log_context(article_id=article_id, attempt=attempt):
        _analyze_claimed(settings, factory, identifier, token, snapshot, article_id)


def _analyze_claimed(settings, factory, identifier, token, snapshot, article_id):
    logger.info("article_analysis_started")
    try:
        # No transaction remains open while waiting for Codex.
        with factory() as session:
            taxonomy = catalog(session)
        with factory.begin() as session:
            job = session.scalar(
                select(ArticleAnalysisJob)
                .where(ArticleAnalysisJob.id == identifier)
                .with_for_update()
            )
            if job is None or job.status != "running" or job.lease_token != token:
                logger.warning("article_analysis_lease_lost")
                return
            job.catalog_snapshot = taxonomy
        output = CodexClient(settings).complete(
            analysis_prompt(snapshot, taxonomy), AnalysisResult.model_json_schema()
        )
        result = AnalysisResult.model_validate(output)
        validate_evidence(result, snapshot, taxonomy)
        with factory.begin() as session:
            job = session.scalar(
                select(ArticleAnalysisJob)
                .where(ArticleAnalysisJob.id == identifier)
                .with_for_update()
            )
            if job is None or job.status != "running" or job.lease_token != token:
                logger.warning("article_analysis_lease_lost")
                return
            job.result = result.model_dump(mode="json")
            # Lock source review before Article, matching ingestion lock order.
            if not approved_sources(session, article_id, lock=True):
                finish_analysis(job, "unapproved")
            else:
                article = session.scalar(
                    select(Article).where(Article.id == article_id).with_for_update(of=Article)
                )
                if article is None:
                    finish_analysis(job, "superseded")
                else:
                    # Deleted catalog IDs or changed topic status cannot bypass
                    # application validation between inference and application.
                    validate_evidence(result, snapshot, catalog(session))
                    apply_analysis(session, article, job, result)
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
        with factory.begin() as session:
            job = session.scalar(
                select(ArticleAnalysisJob)
                .where(ArticleAnalysisJob.id == identifier)
                .with_for_update()
            )
            if job is not None and job.status == "running" and job.lease_token == token:
                fail_analysis(
                    job,
                    reason,
                    retryable=reason
                    not in {
                        "ai_not_configured",
                        "unexpected_tool_execution",
                        "unexpected_server_request",
                    },
                )
        logger.warning("article_analysis_failed", extra={"reason": reason}, exc_info=True)
