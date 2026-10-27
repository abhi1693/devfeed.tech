"""Durable shadow evaluations. Results are private and never publish content."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.json_types import JsonRecord
from devfeed_core.models import PartnerEvaluation, utcnow
from devfeed_core.partner_catalog import lock_catalog
from devfeed_core.partner_matching import save_evaluation_matches
from devfeed_core.partner_tools import (
    EvaluationResult,
    evaluation_prompt,
    snapshot_current,
    validate_result,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import ColumnElement

from devfeed_aggregator.partner_outbox import PartnerOutbox
from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)


def assess(snapshot: JsonRecord) -> JsonRecord:
    from devfeed_aggregator.codex_client import CodexClient

    client = CodexClient(get_settings())
    client.operation = "partner_tool_evaluation"
    return client.complete(evaluation_prompt(snapshot), EvaluationResult.model_json_schema())


def runnable(now: datetime) -> ColumnElement[bool]:
    return or_(
        (PartnerEvaluation.status == "queued") & (PartnerEvaluation.available_at <= now),
        (PartnerEvaluation.status == "running") & (PartnerEvaluation.lease_until < now),
    )


def process_evaluation(
    job_id: str,
    *,
    factory: sessionmaker[Session] | None = None,
    assessor: Callable[[JsonRecord], JsonRecord] | None = None,
) -> None:
    with job_log_context("partner-evaluation", job_id):
        try:
            return _process_evaluation(job_id, factory=factory, assessor=assessor)
        except Exception as exc:
            logger.error(
                "partner_evaluation_runtime_failed", extra={"error_type": type(exc).__name__}
            )
            raise


def _process_evaluation(
    job_id: str,
    *,
    factory: sessionmaker[Session] | None = None,
    assessor: Callable[[JsonRecord], JsonRecord] | None = None,
) -> None:
    if not get_settings().ai_enabled:
        logger.info("partner_evaluation_waiting_for_ai")
        return
    factory = factory or session_factory()
    identifier = uuid.UUID(job_id)
    with factory.begin() as session:
        lock_catalog(session)
        job = session.scalar(
            select(PartnerEvaluation)
            .where(PartnerEvaluation.id == identifier, runnable(utcnow()))
            .with_for_update()
        )
        if job is None:
            logger.debug("partner_evaluation_not_claimed")
            return
        if not snapshot_current(session, job) or job.attempts >= 3:
            job.status, job.error, job.finished_at = (
                "failed",
                "Stale input or retry limit reached",
                utcnow(),
            )
            clear_lease(job)
            logger.warning(
                "partner_evaluation_failed",
                extra={"reason": "stale_input_or_retry_limit", "attempt": job.attempts},
            )
            return
        token = start_job(job, utcnow(), 360)
        snapshot = job.snapshot
        fields = {
            "product_id": str(job.product_id),
            "attempt": job.attempts,
            "articles": len(snapshot["articles"]),
        }
    logger.info("partner_evaluation_started", extra=fields)
    result, error, error_type = None, None, None
    try:
        result = validate_result(snapshot, (assessor or assess)(snapshot))
    except Exception as exc:
        # Provider responses can contain credentials or imported content. Keep diagnostics bounded.
        error = "Evaluation failed or returned unsupported evidence"
        error_type = type(exc).__name__
    with factory.begin() as session:
        lock_catalog(session)
        job = session.scalar(
            select(PartnerEvaluation).where(PartnerEvaluation.id == identifier).with_for_update()
        )
        if not job or job.status != "running" or job.lease_token != token:
            logger.warning(
                "partner_evaluation_result_discarded",
                extra={**fields, "reason": "lease_lost_or_cancelled"},
            )
            return
        if not snapshot_current(session, job):
            logger.info(
                "partner_evaluation_result_discarded", extra={**fields, "reason": "input_changed"}
            )
            fail_or_retry(
                job, "Input changed; automatic rechecking is required", utcnow(), retryable=False
            )
        elif error:
            fail_or_retry(job, error, utcnow())
            logger.warning(
                "partner_evaluation_retry_scheduled"
                if job.status == "queued"
                else "partner_evaluation_failed",
                extra={
                    **fields,
                    "error": error,
                    "error_type": error_type,
                    "retry_at": job.available_at.isoformat() if job.status == "queued" else None,
                },
            )
        else:
            assert result is not None
            job.result = result
            save_evaluation_matches(session, job)
            job.status, job.finished_at, job.error = "succeeded", utcnow(), None
            clear_lease(job)
            logger.info(
                "partner_evaluation_completed",
                extra={
                    **fields,
                    "matches": sum(decision["relevant"] for decision in result["decisions"]),
                },
            )


def dispatch_partner_evaluations(factory: sessionmaker[Session], limit: int | None = None) -> int:
    if limit is not None and limit < 1:
        raise ValueError("Partner dispatch limit must be positive")
    if not get_settings().ai_enabled:
        return 0
    now = utcnow()
    statement = (
        select(PartnerEvaluation)
        .where(
            runnable(now),
            or_(
                PartnerEvaluation.dispatched_at.is_(None),
                PartnerEvaluation.dispatched_at < now - timedelta(seconds=60),
            ),
        )
        .order_by(
            PartnerEvaluation.dispatched_at.asc().nulls_first(),
            PartnerEvaluation.available_at,
            PartnerEvaluation.created_at,
            PartnerEvaluation.id,
        )
    )
    return PartnerOutbox(
        factory, get_queue, batch_size=get_settings().scheduler_batch_size, now=now
    ).publish(
        "source-analysis",
        statement,
        model=PartnerEvaluation,
        limit=limit,
        handler="devfeed_aggregator.partner_tasks.process_evaluation",
        delivery_id=lambda job: f"partner-evaluation-{job.id}-{job.attempts}",
        event="partner_evaluation_dispatched",
        fields=lambda job: {
            "job_kind": "partner-evaluation",
            "job_id": str(job.id),
            "product_id": str(job.product_id),
        },
    )
