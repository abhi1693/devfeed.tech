"""RQ analysis handler: short claims, bounded inference, revision-checked apply."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

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
    require_primary_topic,
    reuse_candidates,
    snapshot_hash,
    source_snapshot,
    validate_evidence,
)
from devfeed_core.analysis_wire import compact_request, restore_identities
from devfeed_core.article_jobs import approved_sources
from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import session_factory
from devfeed_core.inference_validation import feedback_prompt, validation_feedback
from devfeed_core.job_lifecycle import fail_or_retry, finish_job, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
from devfeed_core.logging import log_context
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleContent, utcnow
from devfeed_core.publication_policy import apply_publication_policy
from devfeed_core.topics import lock_topics
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload, sessionmaker

from devfeed_aggregator.analysis_telemetry import record_attempt
from devfeed_aggregator.codex_client import AnalysisError, CodexClient
from devfeed_aggregator.languages import validate_english_ai_prose

logger = logging.getLogger(__name__)


class _ApplicationInputsChanged(Exception):
    """Release publication locks before preparing changed catalog/content inputs."""


def _prepare_application(factory, article_id, snapshot):
    with factory() as session:
        current_catalog = catalog(session)
        article = session.scalar(
            select(Article).where(Article.id == article_id).options(lazyload("*"))
        )
        current = (
            source_snapshot(article, session.get(ArticleContent, article_id))
            if article is not None
            else None
        )
    # Warm both validity checks and any replacement request with no open transaction.
    analysis_candidates(current_catalog, snapshot)
    if current is not None and current != snapshot:
        analysis_candidates(current_catalog, current)
    return current_catalog, current


def _apply_prepared(factory, article_id, snapshot, operation):
    from devfeed_core.transaction_retry import run_transaction

    for attempt in range(3):
        # Do not retain full retrieval indexes during the external inference wait.
        with reuse_snapshots(), reuse_candidates():
            taxonomy, current = _prepare_application(factory, article_id, snapshot)
            try:
                return run_transaction(
                    factory,
                    lambda session, taxonomy=taxonomy, current=current: operation(
                        session, taxonomy, current
                    ),
                )
            except _ApplicationInputsChanged:
                if attempt == 2:
                    raise  # Existing durable dependency retries handle continued churn.


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
        from devfeed_core.analysis import PROMPT_VERSION, VALIDATION_VERSION

        job.prompt_version = PROMPT_VERSION
        job.usage = {**(job.usage or {}), "validation_version": VALIDATION_VERSION}
        job.model = settings.codex_model
        snapshot, article_id = job.input_snapshot, job.article_id
        attempt = job.attempts
    with log_context(article_id=article_id, attempt=attempt):
        ArticleAnalysisService(settings, factory, CodexClient).run(
            AnalysisContext(identifier, token, snapshot, article_id)
        )


@dataclass(frozen=True)
class AnalysisContext:
    identifier: uuid.UUID
    token: uuid.UUID
    snapshot: dict
    article_id: uuid.UUID


class AnalysisClient(Protocol):
    operation: str
    job_id: uuid.UUID | None
    attempt: int | None
    reason: str | None
    quality_failure: bool

    def complete(self, prompt: str, schema: dict) -> dict: ...


@dataclass(frozen=True)
class PreparedAnalysis:
    taxonomy: dict
    feedback: dict | None
    compact: bool
    reason: str


@dataclass
class AnalysisAttempt:
    started: float = field(default_factory=time.perf_counter)
    client: AnalysisClient | None = None
    number: int | None = None
    output: dict | None = None


@dataclass(frozen=True)
class ArticleAnalysisService:
    """Own one claimed job's I/O and transaction boundaries.

    Inference never holds a transaction. Application and retries always verify
    the original lease; transaction retries never repeat external inference.
    """

    settings: Settings
    factory: sessionmaker[Session]
    client_factory: Callable[[Settings], AnalysisClient]

    def run(self, context: AnalysisContext) -> None:
        logger.info("article_analysis_started")
        attempt = AnalysisAttempt()
        try:
            prepared = self.prepare(context, attempt)
            if prepared is None:
                return
            result, topic_match_status = self.infer(context, prepared, attempt)
            self.propose_topics(context, result)
            outcome = _apply_prepared(
                self.factory,
                context.article_id,
                context.snapshot,
                lambda session, taxonomy, current: self.apply(
                    session, context, result, topic_match_status, attempt, taxonomy, current
                ),
            )
            logger.info("article_analysis_completed", extra={"outcome": outcome})
        except Exception as exc:
            self.fail(context, attempt, exc)
        finally:
            if attempt.client is not None and attempt.number is not None:
                record_attempt(
                    self.factory,
                    ArticleAnalysisJob,
                    context.identifier,
                    attempt.client,
                    attempt.started,
                    attempt=attempt.number,
                )

    def prepare(
        self, context: AnalysisContext, attempt: AnalysisAttempt
    ) -> PreparedAnalysis | None:
        with self.factory() as session:
            inference_catalog = catalog(session)
        taxonomy = analysis_candidates(inference_catalog, context.snapshot)
        del inference_catalog
        with self.factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
            if job is None:
                logger.warning("article_analysis_lease_lost")
                return None
            job.catalog_snapshot = taxonomy
            job.catalog_hash = snapshot_hash(taxonomy)
            attempt.number = job.attempts
            feedback = (job.result or {}).get("validation_feedback")
            compact = getattr(self.settings, "ai_compact_article_prompts", False)
            job.usage = {
                **(job.usage or {}),
                "prompt_format": "compact-evidence-v3" if compact else "original",
            }
            return PreparedAnalysis(
                taxonomy, feedback, compact, job.usage.get("requested_reason", "queued_analysis")
            )

    def infer(
        self, context: AnalysisContext, prepared: PreparedAnalysis, attempt: AnalysisAttempt
    ) -> tuple[AnalysisResult, str | None]:
        client = attempt.client = self.client_factory(self.settings)
        client.operation = "article_analysis"
        client.job_id, client.attempt = context.identifier, attempt.number
        client.reason = prepared.reason
        client.quality_failure = bool(prepared.feedback) and attempt.number == 2
        if prepared.compact:
            prompt, schema, identities = compact_request(
                context.snapshot, prepared.taxonomy, evidence_refs=True
            )
        else:
            prompt, schema = (
                analysis_prompt(context.snapshot, prepared.taxonomy),
                analysis_output_schema(prepared.taxonomy),
            )
        correction = feedback_prompt(prepared.feedback)
        if prepared.compact:
            correction = correction.replace(
                "exact verbatim evidence", "source passage IDs for evidence"
            )
        attempt.output = client.complete(prompt + correction, schema)
        if prepared.compact:
            attempt.output = restore_identities(attempt.output, identities)
        result, topic_match_status = require_primary_topic(
            AnalysisResult.model_validate(attempt.output)
        )
        validate_english_ai_prose(result.ai_summary, result.ai_description)
        validate_evidence(result, context.snapshot, prepared.taxonomy)
        return result, topic_match_status

    def propose_topics(self, context: AnalysisContext, result: AnalysisResult) -> None:
        if not (
            self.settings.full_automation
            and result.developer_relevance == "relevant"
            and result.page_kind == "article"
        ):
            return
        from devfeed_core.article_automation import propose_source_topics

        # Release proposal locks before classification and publication.
        with self.factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
            if job is None:
                logger.warning("article_analysis_lease_lost")
                return
            source_ids = approved_sources(session, context.article_id, lock=True)
            article = self.lock_article(session, context)
            if article is not None and source_ids and article.review_status != "rejected":
                propose_source_topics(session, article)

    @staticmethod
    def lock_article(session: Session, context: AnalysisContext) -> Article | None:
        return session.scalar(
            select(Article).where(Article.id == context.article_id).with_for_update(of=Article)
        )

    def apply(
        self,
        session: Session,
        context: AnalysisContext,
        result: AnalysisResult,
        topic_match_status: str | None,
        attempt: AnalysisAttempt,
        current_catalog: dict,
        prepared_snapshot: dict | None,
    ) -> str | None:
        job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
        if job is None:
            logger.warning("article_analysis_lease_lost")
            return None
        job.result = result.model_dump(mode="json")
        if topic_match_status:
            job.result["topic_match_status"] = topic_match_status
        job.model = getattr(attempt.client, "model", self.settings.codex_model)
        # Match ingestion's Source -> Article -> catalog lock order.
        if not approved_sources(session, context.article_id, lock=True):
            finish_job(job, "unapproved", utcnow())
            return job.outcome
        article = self.lock_article(session, context)
        if article is None:
            finish_job(job, "superseded", utcnow())
        elif not eligible_article(article):
            finish_job(job, "content_date_deferred", utcnow())
        else:
            self.apply_current(
                session, context, result, article, job, current_catalog, prepared_snapshot
            )
        return job.outcome

    @staticmethod
    def apply_current(
        session, context, result, article, job, current_catalog, prepared_snapshot
    ) -> None:
        lock_topics(session, read=True)
        if source_snapshot(
            article, session.get(ArticleContent, context.article_id)
        ) != prepared_snapshot or not snapshot_current(
            session, ("topics", "tags"), current_catalog
        ):
            raise _ApplicationInputsChanged()
        if not analysis_catalog_current(job, current_catalog, context.snapshot):
            finish_job(job, "superseded", utcnow())
        else:
            validate_evidence(result, context.snapshot, current_catalog)
            apply_analysis(session, article, job, result)
            if job.outcome in {"applied", "insufficient_evidence"}:
                apply_publication_policy(session, article, job, taxonomy=current_catalog)
        refresh_superseded_analysis(session, article, job, taxonomy=current_catalog)

    def fail(self, context: AnalysisContext, attempt: AnalysisAttempt, exc: Exception) -> None:
        feedback = validation_feedback(exc)
        reason = (
            str(exc)
            if isinstance(exc, AnalysisError)
            else "invalid_analysis_result"
            if isinstance(exc, (ValueError, ValidationError))
            else "analysis_dependency_failure"
        )
        cooldown = (
            safe_pause(getattr(exc, "retry_after", 0), reason=reason)
            if reason in CAPACITY_ERRORS
            else 0
        )
        with self.factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
            if job is not None:
                repeated = self.save_feedback(job, attempt, feedback)
                fail_analysis(job, reason, retry_after=cooldown)
                if (
                    feedback
                    and (attempt.number or 0) >= 2
                    and reason not in CAPACITY_ERRORS
                    and (getattr(self.settings, "ai_tiered_routing_enabled", False) or repeated)
                ):
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

    @staticmethod
    def save_feedback(job, attempt: AnalysisAttempt, feedback: dict | None) -> bool:
        if feedback is None:
            return False
        repeated = False
        if isinstance(attempt.output, dict):
            digest = snapshot_hash(attempt.output)
            repeated = (job.usage or {}).get("invalid_output_hash") == digest
            job.usage = {**(job.usage or {}), "invalid_output_hash": digest}
        job.result = {**(job.result or {}), "validation_feedback": feedback}
        return repeated
