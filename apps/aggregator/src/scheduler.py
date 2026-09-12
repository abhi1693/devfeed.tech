import argparse
import logging
import signal
import threading
import time
import uuid
from contextlib import suppress

from devfeed_core.analysis import fail_analysis
from devfeed_core.automation_scheduler import schedule_automation
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.engagement import prune_article_opens
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.jobs import fail_job, request_ingestion
from devfeed_core.logging import configure_logging, elapsed_ms, log_context
from devfeed_core.models import (
    IngestionJob,
    Source,
    utcnow,
)
from devfeed_core.overview_daily import refresh_overview_daily
from devfeed_core.recommendations import (
    dispatch_recommendations,
    expand_recommendation_events,
)
from devfeed_core.research_verification import fail_verification, schedule_verification
from devfeed_core.version import __version__
from sqlalchemy import select

from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)


def tick() -> dict[str, int]:
    with log_context(service="scheduler", tick_id=str(uuid.uuid4())):
        started = time.perf_counter()
        try:
            result = _tick()
        except Exception:
            logger.exception("scheduler_tick_failed", extra={"duration_ms": elapsed_ms(started)})
            raise
        level = (
            logging.WARNING
            if result["recovered"]
            or result["images_recovered"]
            or result.get("profiles_recovered")
            or result.get("articles_recovered")
            else (logging.INFO if any(result.values()) else logging.DEBUG)
        )
        logger.log(
            level,
            "scheduler_tick_completed",
            extra={**result, "duration_ms": elapsed_ms(started)},
        )
        return result


def _tick() -> dict[str, int]:
    factory = session_factory()
    try:
        refresh_overview_daily(factory)
    except Exception:
        # Retain source events if aggregation fails, without stopping feed ingestion.
        logger.exception("overview_daily_refresh_failed")
    else:
        prune_article_opens(factory)
    recommendation_users_queued = expand_recommendation_events(
        factory, get_settings().scheduler_batch_size
    )
    automation = schedule_automation(factory)
    verifications_scheduled = schedule_verification(factory)
    batch = get_settings().scheduler_batch_size
    now = utcnow()
    recovered = scheduled = dispatched = 0
    recovery_logs = []
    with factory.begin() as session:
        expired = session.scalars(
            select(IngestionJob.id)
            .join(Source, Source.id == IngestionJob.source_id)
            .where(
                IngestionJob.status == "running",
                IngestionJob.lease_until < now,
            )
            .order_by(IngestionJob.lease_until)
            .limit(batch)
            .with_for_update(of=Source, skip_locked=True)
        ).all()
        for job_id in expired:
            # Match source deletion's lock order and recheck after obtaining the
            # job lock: a dispatcher or worker may have changed its lease.
            expired_job = session.scalar(
                select(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.status == "running",
                    IngestionJob.lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
            if expired_job is None:
                continue
            fail_job(session, expired_job, "Worker lease expired; interrupted job recovered")
            recovered += 1
            recovery_logs.append(
                {
                    "job_id": expired_job.id,
                    "source_id": expired_job.source_id,
                    "job_status": expired_job.status,
                    "attempt": expired_job.attempts,
                }
            )
    for fields in recovery_logs:
        logger.warning("ingestion_lease_recovered", extra=fields)
    scheduled_logs = []
    with factory.begin() as session:
        # Exclude active sources before LIMIT so unhealthy/slow feeds cannot starve others.
        active = select(IngestionJob.id).where(
            IngestionJob.source_id == Source.id,
            IngestionJob.status.in_(["queued", "running"]),
        )
        sources = session.scalars(
            select(Source)
            .where(
                Source.enabled.is_(True),
                Source.approval_status == "approved",
                Source.next_fetch_at <= now,
                ~active.exists(),
            )
            .order_by(Source.next_fetch_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for source in sources:
            job = request_ingestion(session, source)
            scheduled_logs.append({"job_id": job.id, "source_id": source.id})
            scheduled += 1
    for scheduled_fields in scheduled_logs:
        logger.info("ingestion_scheduled", extra=scheduled_fields)
    images_recovered = recover_jobs(factory, batch, now, kind="images")
    profiles_recovered = recover_jobs(factory, batch, now, kind="source-enrichment")
    articles_recovered = recover_jobs(factory, batch, now, kind="article-enrichment")
    analyses_recovered = recover_jobs(factory, batch, now)
    topic_analyses_recovered = recover_jobs(factory, batch, now, kind="topic-analysis")
    verifications_recovered = recover_jobs(factory, batch, now, kind="research-verification")
    notifications_dispatched = notifications_recovered = 0
    if get_settings().notifications_enabled:
        from devfeed_core.feed_notifications import expand_feed_notifications
        from devfeed_notifications.delivery import recover_notifications

        expand_feed_notifications(factory)

        notifications_recovered = recover_notifications(factory, batch, now)
        notification_queue = get_queue("notifications")
        try:
            notifications_dispatched = dispatch_jobs(
                factory, notification_queue, batch, utcnow(), kind="notifications"
            )
        finally:
            notification_queue.connection.close()
    # The PostgreSQL job row is a durable outbox. Publish before stamping dispatch;
    # if we crash between them, re-delivery is safe because claims are exclusive.
    queue = get_queue()
    now = utcnow()  # Include jobs created during the scheduling transaction above.
    try:
        recommendations_dispatched = dispatch_recommendations(factory, queue, batch)
        dispatched = dispatch_jobs(factory, queue, batch, now)
        images_dispatched = dispatch_jobs(factory, queue, batch, now, kind="images")
        profiles_dispatched = dispatch_jobs(factory, queue, batch, now, kind="source-enrichment")
        articles_dispatched = dispatch_jobs(factory, queue, batch, now, kind="article-enrichment")
        if get_settings().solver_queue_enabled:
            solver_queue = get_queue("solver")
            try:
                profiles_dispatched += dispatch_jobs(
                    factory, solver_queue, batch, now, kind="source-enrichment", solver=True
                )
                articles_dispatched += dispatch_jobs(
                    factory, solver_queue, batch, now, kind="article-enrichment", solver=True
                )
                images_dispatched += dispatch_jobs(
                    factory, solver_queue, batch, now, kind="images", solver=True
                )
            finally:
                solver_queue.connection.close()
        analyses_dispatched = topic_analyses_dispatched = 0
        verifications_dispatched = 0
        if get_settings().ai_enabled:
            analysis_queue = get_queue("analysis")
            relationship_queue = get_queue("relationships")
            try:
                profiles_dispatched += dispatch_jobs(
                    factory,
                    analysis_queue,
                    batch,
                    now,
                    kind="source-enrichment",
                    source_analysis=True,
                )
                if get_settings().auto_approve_topics:
                    verifications_dispatched += dispatch_jobs(
                        factory,
                        analysis_queue,
                        batch,
                        now,
                        kind="research-verification",
                        relationships=False,
                    )
                if get_settings().auto_approve_topic_relationships:
                    verifications_dispatched += dispatch_jobs(
                        factory,
                        relationship_queue,
                        batch,
                        now,
                        kind="research-verification",
                        relationships=True,
                    )
                analyses_dispatched = dispatch_jobs(
                    factory, analysis_queue, batch, now, kind="analysis"
                )
                topic_analyses_dispatched = dispatch_jobs(
                    factory,
                    analysis_queue,
                    batch,
                    now,
                    kind="topic-analysis",
                    relationships=False,
                )
                topic_analyses_dispatched += dispatch_jobs(
                    factory,
                    relationship_queue,
                    batch,
                    now,
                    kind="topic-analysis",
                    relationships=True,
                )
            finally:
                analysis_queue.connection.close()
                relationship_queue.connection.close()
        queue.connection.set("devfeed:scheduler:heartbeat", now.isoformat(), ex=120)
    finally:
        queue.connection.close()
    return {
        **automation,
        "recommendation_users_queued": recommendation_users_queued,
        "recommendations_dispatched": recommendations_dispatched,
        "scheduled": scheduled,
        "dispatched": dispatched,
        "recovered": recovered,
        "images_dispatched": images_dispatched,
        "images_recovered": images_recovered,
        "profiles_dispatched": profiles_dispatched,
        "profiles_recovered": profiles_recovered,
        "articles_dispatched": articles_dispatched,
        "articles_recovered": articles_recovered,
        "analyses_dispatched": analyses_dispatched,
        "analyses_recovered": analyses_recovered,
        "topic_analyses_dispatched": topic_analyses_dispatched,
        "topic_analyses_recovered": topic_analyses_recovered,
        "verifications_scheduled": verifications_scheduled,
        "verifications_dispatched": verifications_dispatched,
        "verifications_recovered": verifications_recovered,
        "notifications_dispatched": notifications_dispatched,
        "notifications_recovered": notifications_recovered,
    }


RECOVERY_ERRORS = {
    "images": "Worker lease expired; interrupted image lookup recovered",
    "source-enrichment": "Worker lease expired; interrupted source enrichment recovered",
    "article-enrichment": "Worker lease expired; interrupted article lookup recovered",
}


def recover_jobs(factory, batch, now, *, kind="analysis") -> int:
    if kind not in RECOVERY_ERRORS and kind not in {
        "analysis",
        "topic-analysis",
        "research-verification",
    }:
        raise ValueError("This pipeline requires its own recovery policy")
    definition = JOB_DEFINITIONS[kind]
    model = definition.model
    reason = RECOVERY_ERRORS.get(kind, "worker_lease_expired")
    recovered = []
    with factory.begin() as session:
        jobs = session.scalars(
            select(model)
            .where(model.status == "running", model.lease_until < now)
            .order_by(model.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            if kind == "research-verification":
                fail_verification(job, reason)
            elif kind in {"analysis", "topic-analysis"}:
                fail_analysis(job, reason)
            else:
                fail_or_retry(job, reason, utcnow())
            recovered.append({**definition.log_fields(job), "job_status": job.status})
    if kind in RECOVERY_ERRORS:
        for fields in recovered:
            logger.warning(definition.event + "_lease_recovered", extra=fields)
    return len(recovered)


def run() -> None:
    settings = get_settings()
    configure_logging("scheduler", settings.log_level, settings.log_format)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    with log_context(service="scheduler"):
        logger.info("scheduler_started")
        try:
            while not stop.is_set():
                # tick() logs safe diagnostics; keep the recurring scheduler alive.
                with suppress(Exception):
                    tick()
                stop.wait(15)
        finally:
            logger.info("scheduler_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Durable feed scheduling and RQ dispatch")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        settings = get_settings()
        configure_logging("scheduler", settings.log_level, settings.log_format)
        try:
            tick()
        except Exception:
            raise SystemExit(1) from None
    else:
        run()
