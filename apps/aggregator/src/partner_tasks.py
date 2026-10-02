"""Durable shadow evaluations. Results are private and never publish content."""

import uuid
from datetime import timedelta

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, start_job
from devfeed_core.models import PartnerEvaluation, utcnow
from devfeed_core.partner_tools import (
    EvaluationResult,
    evaluation_prompt,
    snapshot_current,
    validate_result,
)
from sqlalchemy import or_, select

from devfeed_aggregator.queue import get_queue


def assess(snapshot):
    from devfeed_aggregator.codex_client import CodexClient

    client = CodexClient(get_settings())
    client.operation = "partner_tool_evaluation"
    return client.complete(evaluation_prompt(snapshot), EvaluationResult.model_json_schema())


def runnable(now):
    return or_(
        (PartnerEvaluation.status == "queued") & (PartnerEvaluation.available_at <= now),
        (PartnerEvaluation.status == "running") & (PartnerEvaluation.lease_until < now),
    )


def process_evaluation(job_id: str, *, factory=None, assessor=None):
    if not get_settings().ai_enabled:
        return
    factory = factory or session_factory()
    identifier = uuid.UUID(job_id)
    with factory.begin() as session:
        job = session.scalar(
            select(PartnerEvaluation)
            .where(PartnerEvaluation.id == identifier, runnable(utcnow()))
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return
        if not snapshot_current(session, job) or job.attempts >= 3:
            job.status, job.error, job.finished_at = (
                "failed",
                "Stale input or retry limit reached",
                utcnow(),
            )
            clear_lease(job)
            return
        token = start_job(job, utcnow(), 360)
        snapshot = job.snapshot
    result, error = None, None
    try:
        result = validate_result(snapshot, (assessor or assess)(snapshot))
    except Exception:
        # Provider responses can contain credentials or imported content. Keep diagnostics bounded.
        error = "Evaluation failed or returned unsupported evidence"
    with factory.begin() as session:
        job = session.scalar(
            select(PartnerEvaluation).where(PartnerEvaluation.id == identifier).with_for_update()
        )
        if not job or job.status != "running" or job.lease_token != token:
            return
        if not snapshot_current(session, job):
            fail_or_retry(
                job, "Input changed; approve and evaluate again", utcnow(), retryable=False
            )
        elif error:
            fail_or_retry(job, error, utcnow())
        else:
            job.result = result
            job.status, job.finished_at, job.error = "succeeded", utcnow(), None
            clear_lease(job)


def dispatch_partner_evaluations(factory, limit=2):
    if not get_settings().ai_enabled:
        return 0
    now = utcnow()
    queue = get_queue("source-analysis")
    dispatched = 0
    try:
        with factory.begin() as session:
            jobs = session.scalars(
                select(PartnerEvaluation)
                .where(
                    runnable(now),
                    or_(
                        PartnerEvaluation.dispatched_at.is_(None),
                        PartnerEvaluation.dispatched_at < now - timedelta(seconds=60),
                    ),
                )
                .order_by(PartnerEvaluation.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                queue.enqueue(
                    "devfeed_aggregator.partner_tasks.process_evaluation",
                    str(job.id),
                    job_id=f"partner-evaluation-{job.id}-{job.attempts}",
                    unique=True,
                    job_timeout=300,
                    result_ttl=0,
                    failure_ttl=60,
                )
                job.dispatched_at = now
                dispatched += 1
    finally:
        queue.connection.close()
    return dispatched
