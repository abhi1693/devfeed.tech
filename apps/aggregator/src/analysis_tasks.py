"""RQ analysis handler: short claims, bounded inference, revision-checked apply."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Protocol

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.ai_content import eligible_article
from devfeed_core.analysis import (
    TERMINAL_ANALYSIS_ERRORS,
    AnalysisCatalog,
    AnalysisResult,
    SourceSnapshot,
    analysis_candidates,
    analysis_catalog_current,
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
from devfeed_core.article_analysis_steps import (
    STEP_SPECS,
    article_step_feedback,
    assemble_article_analysis,
    next_article_step,
    prepare_article_step,
    validate_article_step,
)
from devfeed_core.article_jobs import approved_sources
from devfeed_core.article_quality import identifying_title
from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import session_factory
from devfeed_core.inference_validation import ValidationFeedback, validation_feedback
from devfeed_core.job_lifecycle import fail_or_retry, finish_job, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
from devfeed_core.json_types import JsonRecord
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


class _AnalysisStopped(Exception):
    """A lost lease or superseded input stops further external inference."""


def _prepare_application(
    factory: sessionmaker[Session], article_id: uuid.UUID, snapshot: SourceSnapshot
) -> tuple[AnalysisCatalog, SourceSnapshot | None]:
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


def _apply_prepared[T](
    factory: sessionmaker[Session],
    article_id: uuid.UUID,
    snapshot: SourceSnapshot,
    operation: Callable[[Session, AnalysisCatalog, SourceSnapshot | None], T],
) -> T | None:
    from devfeed_core.transaction_retry import run_transaction

    for attempt in range(3):
        # Do not retain full retrieval indexes during the external inference wait.
        with reuse_snapshots(), reuse_candidates():
            taxonomy, current = _prepare_application(factory, article_id, snapshot)
            try:

                def apply(
                    session: Session,
                    taxonomy: AnalysisCatalog = taxonomy,
                    current: SourceSnapshot | None = current,
                ) -> T:
                    return operation(session, taxonomy, current)

                return run_transaction(factory, apply)
            except _ApplicationInputsChanged:
                if attempt == 2:
                    raise  # Existing durable dependency retries handle continued churn.
    return None


def analyze_article(job_id: str) -> None:
    with job_log_context("analysis", job_id):
        try:
            _analyze(uuid.UUID(job_id))
        except Exception:
            logger.exception("article_analysis_runtime_failed")
            raise


def _analyze(identifier: uuid.UUID) -> None:
    settings, factory = get_settings(), session_factory()
    with factory.begin() as session:
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.id == identifier).with_for_update()
        )
        if job is None or job.status != "queued" or job.available_at > utcnow():
            logger.debug("article_analysis_not_claimed")
            return
        job.usage = {**(job.usage or {}), "active_step": None}
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
        if article.publication_status == "published":
            # Refresh promotion needs a separate candidate snapshot. Do not spend
            # inference capacity or mix unfinished refresh fields into live data.
            finish_job(job, "published_preserved", utcnow())
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
    snapshot: SourceSnapshot
    article_id: uuid.UUID


class AnalysisClient(Protocol):
    operation: str
    job_id: uuid.UUID | str | None
    attempt: int | None
    reason: str | None
    quality_failure: bool

    def complete(self, prompt: str, schema: JsonRecord) -> JsonRecord: ...


@dataclass(frozen=True)
class PreparedAnalysis:
    taxonomy: AnalysisCatalog
    feedback: JsonRecord | None
    compact: bool
    reason: str


@dataclass
class AnalysisAttempt:
    started: float = field(default_factory=time.perf_counter)
    client: AnalysisClient | None = None
    number: int | None = None
    output: JsonRecord | None = None
    step: str | None = None
    clients: list[object] = field(default_factory=list)


@dataclass(frozen=True)
class ArticleAnalysisService:
    """Own one claimed job's I/O and transaction boundaries.

    Inference never holds a transaction. Application and retries always verify
    the original lease; transaction retries never repeat external inference.
    """

    settings: Settings
    factory: sessionmaker[Session]
    client_factory: Callable[[Settings], AnalysisClient]

    def routing_binding(self) -> str:
        return snapshot_hash(
            {
                field: getattr(self.settings, field, None)
                for field in (
                    "codex_model",
                    "ai_tiered_routing_enabled",
                    "ai_fast_model",
                    "ai_escalation_model",
                )
            }
        )

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
        except _AnalysisStopped:
            logger.info("article_analysis_stopped")
        except Exception as exc:
            self.fail(context, attempt, exc)
        finally:
            if attempt.clients and attempt.number is not None:
                counts: dict[str, int] = {}
                for client in attempt.clients:
                    for name, value in getattr(client, "usage", {}).items():
                        counts[name] = counts.get(name, 0) + value
                aggregate = SimpleNamespace(
                    usage=counts,
                    model=getattr(attempt.client, "model", self.settings.codex_model),
                    reasoning_effort=getattr(attempt.client, "reasoning_effort", None),
                    web_search_count=sum(
                        getattr(client, "web_search_count", 0) for client in attempt.clients
                    ),
                )
                record_attempt(
                    self.factory,
                    ArticleAnalysisJob,
                    context.identifier,
                    aggregate,
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
            job.usage = {
                **(job.usage or {}),
                "prompt_format": "specialist-steps-v1",
            }
            return PreparedAnalysis(
                taxonomy, feedback, False, job.usage.get("requested_reason", "queued_analysis")
            )

    def infer(
        self, context: AnalysisContext, prepared: PreparedAnalysis, attempt: AnalysisAttempt
    ) -> tuple[AnalysisResult, str | None]:
        outputs: dict[str, JsonRecord] = {}
        while name := next_article_step(context.snapshot, outputs):
            prompt, schema, digest = prepare_article_step(
                name, context.snapshot, prepared.taxonomy, outputs
            )
            with self.factory.begin() as session:
                job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
                if job is None:
                    raise _AnalysisStopped()
                cached = dict(job.steps.get(name, {}))
            if (
                cached.get("status") == "succeeded"
                and cached.get("input_hash") == digest
                and cached.get("prompt_version") == STEP_SPECS[name].prompt_version
                and cached.get("model") == self.settings.codex_model
                and cached.get("routing_binding") == self.routing_binding()
            ):
                try:
                    output = self.validate_step(
                        name, cached.get("output"), context, prepared, outputs
                    )
                except (ValueError, ValidationError):
                    pass  # Invalid persisted data cannot become publication evidence.
                else:
                    outputs[name] = output
                    continue
            step = self.start_step(context, name, digest)
            if step is None:
                raise _AnalysisStopped()
            attempt.step, attempt.output = name, None
            client = attempt.client = self.client_factory(self.settings)
            client.operation = "article_analysis"
            client.job_id, client.attempt = context.identifier, attempt.number
            client.reason = f"{prepared.reason}:{name}"
            client.quality_failure = bool(step.get("validation_feedback"))
            try:
                attempt.output = client.complete(
                    prompt + article_step_feedback(name, step.get("validation_feedback")), schema
                )
            finally:
                # complete() resets transport counters; copy each call before a
                # reused client can start another specialist.
                attempt.clients.append(
                    SimpleNamespace(
                        usage=dict(getattr(client, "usage", {})),
                        web_search_count=getattr(client, "web_search_count", 0),
                    )
                )
            output = self.validate_step(name, attempt.output, context, prepared, outputs)
            self.complete_step(context, name, output, client)
            outputs[name] = output
            attempt.step = None
        result = assemble_article_analysis(context.snapshot, outputs)
        result, topic_match_status = (
            require_primary_topic(result) if "topics" in outputs else (result, None)
        )
        if outputs.get("topics", {}).get("outcome") == "insufficient_evidence" and not any(
            item.role == "primary" for item in result.topics
        ):
            topic_match_status = "no_topic_match"
        validate_english_ai_prose(result.ai_summary, result.ai_description)
        validate_evidence(result, context.snapshot, prepared.taxonomy)
        return result, topic_match_status

    @staticmethod
    def validate_step(
        name: str,
        output: JsonRecord | None,
        context: AnalysisContext,
        prepared: PreparedAnalysis,
        outputs: dict[str, JsonRecord],
    ) -> JsonRecord:
        assert output is not None  # Validation runs only after a returned specialist response.
        value = validate_article_step(name, output, context.snapshot, prepared.taxonomy, outputs)
        if name == "overview":
            validate_english_ai_prose(value.get("ai_summary"), None)
        elif name == "description":
            validate_english_ai_prose(None, value.get("ai_description"))
        return value

    def start_step(self, context: AnalysisContext, name: str, digest: str) -> JsonRecord | None:
        with self.factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
            if job is None:
                raise _AnalysisStopped()
            if not approved_sources(session, context.article_id, lock=True):
                finish_job(job, "unapproved", utcnow())
                return None
            article = self.lock_article(session, context)
            if article is None or (
                article.review_status == "rejected"
                or article.editorial_revision != job.editorial_revision
                or snapshot_hash(source_snapshot(article, session.get(ArticleContent, article.id)))
                != job.input_hash
            ):
                finish_job(job, "superseded", utcnow())
                if article is not None:
                    refresh_superseded_analysis(session, article, job)
                return None
            if article.publication_status == "published" or not eligible_article(article):
                finish_job(
                    job,
                    "published_preserved"
                    if article.publication_status == "published"
                    else "content_date_deferred",
                    utcnow(),
                )
                return None
            steps = dict(job.steps)
            prior = dict(steps.get(name, {}))
            same_input = (
                prior.get("input_hash") == digest
                and prior.get("prompt_version") == STEP_SPECS[name].prompt_version
                and prior.get("model") == self.settings.codex_model
                and prior.get("routing_binding") == self.routing_binding()
            )
            # Each specialist proves its own exact context. Independent valid
            # outputs survive other task changes; dependent contexts (such as a
            # description's accepted overview) invalidate through their own hash.
            if identifying_title(context.snapshot.get("title")):
                steps["identifying_title"] = {
                    "status": "skipped",
                    "prompt_version": STEP_SPECS["identifying_title"].prompt_version,
                    "input_hash": snapshot_hash({"source_title": context.snapshot.get("title")}),
                    "attempts": 0,
                    "output": None,
                    "reason": "source_title_identifies_subject",
                }
            now = utcnow()
            step = {
                **(prior if same_input else {}),
                "status": "running",
                "prompt_version": STEP_SPECS[name].prompt_version,
                "input_hash": digest,
                "model": self.settings.codex_model,
                "routing_binding": self.routing_binding(),
                "attempts": (prior.get("attempts", 0) if same_input else 0) + 1,
                "started_at": now.isoformat(),
                "finished_at": None,
                "error": None,
                "output": None,
            }
            steps[name] = step
            job.steps = steps
            job.usage = {**job.usage, "active_step": name}
            job.lease_until = now + timedelta(
                seconds=max(300, getattr(self.settings, "codex_timeout_seconds", 90) + 60)
            )
            return step

    def complete_step(
        self, context: AnalysisContext, name: str, output: JsonRecord, client: AnalysisClient
    ) -> None:
        with self.factory.begin() as session:
            job = owned_job(session, ArticleAnalysisJob, context.identifier, context.token)
            if job is None:
                raise _AnalysisStopped()
            step = dict(job.steps[name])
            step.update(
                status="succeeded",
                output=output,
                finished_at=utcnow().isoformat(),
                error=None,
                tokens=dict(getattr(client, "usage", {})),
                resolved_model=getattr(client, "model", self.settings.codex_model),
            )
            job.steps = {**job.steps, name: step}
            job.usage = {**job.usage, "active_step": None}

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
        current_catalog: AnalysisCatalog,
        prepared_snapshot: SourceSnapshot | None,
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
        elif article.publication_status == "published":
            finish_job(job, "published_preserved", utcnow())
        else:
            self.apply_current(
                session, context, result, article, job, current_catalog, prepared_snapshot
            )
        return job.outcome

    @staticmethod
    def apply_current(
        session: Session,
        context: AnalysisContext,
        result: AnalysisResult,
        article: Article,
        job: ArticleAnalysisJob,
        current_catalog: AnalysisCatalog,
        prepared_snapshot: SourceSnapshot | None,
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
                if attempt.step is not None:
                    self.fail_step(job, attempt, feedback, reason, cooldown)
                else:
                    self.save_feedback(job, attempt, feedback)
                    # An aggregate contract failure after every validated task
                    # cannot be repaired by repeating paid specialist calls.
                    fail_analysis(
                        job,
                        reason,
                        retry_after=cooldown,
                        retryable=reason == "analysis_dependency_failure"
                        or reason in CAPACITY_ERRORS,
                    )
                job.usage = {**(job.usage or {}), "active_step": attempt.step}
        logger.warning(
            "article_analysis_failed",
            extra={
                "reason": reason,
                "validation_code": feedback["code"] if feedback else None,
                "validation_fields": feedback["fields"] if feedback else [],
            },
            exc_info=True,
        )

    def fail_step(
        self,
        job: ArticleAnalysisJob,
        attempt: AnalysisAttempt,
        feedback: ValidationFeedback | None,
        reason: str,
        cooldown: int,
    ) -> None:
        name = attempt.step
        assert name is not None  # This branch is entered only for an active step.
        step = dict(job.steps[name])
        repeated = False
        if feedback and isinstance(attempt.output, dict):
            digest = snapshot_hash(attempt.output)
            repeated = step.get("invalid_output_hash") == digest
            step["invalid_output_hash"] = digest
        if feedback:
            step["validation_feedback"] = feedback
        if reason in CAPACITY_ERRORS:
            step["capacity_deferrals"] = step.get("capacity_deferrals", 0) + 1
            fail_analysis(job, reason, retry_after=cooldown)
        else:
            effective = max(1, step["attempts"] - step.get("capacity_deferrals", 0))
            fail_or_retry(
                job,
                reason,
                utcnow(),
                retryable=reason not in TERMINAL_ANALYSIS_ERRORS and not repeated,
                retry_after=cooldown,
                attempts=effective,
            )
        step.update(
            status="pending" if job.status == "queued" else "failed",
            error=reason,
            finished_at=utcnow().isoformat(),
            available_at=job.available_at.isoformat(),
            tokens=dict(getattr(attempt.client, "usage", {})),
        )
        job.steps = {**job.steps, name: step}

    @staticmethod
    def save_feedback(
        job: ArticleAnalysisJob, attempt: AnalysisAttempt, feedback: ValidationFeedback | None
    ) -> bool:
        if feedback is None:
            return False
        repeated = False
        if isinstance(attempt.output, dict):
            digest = snapshot_hash(attempt.output)
            repeated = (job.usage or {}).get("invalid_output_hash") == digest
            job.usage = {**(job.usage or {}), "invalid_output_hash": digest}
        job.result = {**(job.result or {}), "validation_feedback": feedback}
        return repeated
