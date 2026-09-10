import argparse
import logging
import signal
import threading
import time
import uuid
from contextlib import suppress
from datetime import timedelta

from devfeed_core.analysis import fail_analysis
from devfeed_core.article_jobs import fail_article
from devfeed_core.automation_scheduler import schedule_automation
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.image_jobs import fail_image
from devfeed_core.jobs import JOB_TIMEOUT_SECONDS, REDISPATCH_SECONDS, fail_job, request_ingestion
from devfeed_core.logging import configure_logging, elapsed_ms, log_context
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    TopicAnalysisJob,
    utcnow,
)
from devfeed_core.source_enrichment import fail_enrichment
from devfeed_core.version import __version__
from sqlalchemy import or_, select

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
            level, "scheduler_tick_completed", extra={**result, "duration_ms": elapsed_ms(started)}
        )
        return result


def _tick() -> dict[str, int]:
    factory = session_factory()
    automation = schedule_automation(factory)
    batch = get_settings().scheduler_batch_size
    now = utcnow()
    recovered = scheduled = dispatched = 0
    recovery_logs = []
    with factory.begin() as session:
        expired = session.scalars(
            select(IngestionJob)
            .where(
                IngestionJob.status == "running",
                IngestionJob.lease_until < now,
            )
            .order_by(IngestionJob.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for expired_job in expired:
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
            IngestionJob.source_id == Source.id, IngestionJob.status.in_(["queued", "running"])
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
    images_recovered = recover_image_jobs(factory, batch, now)
    profiles_recovered = recover_source_jobs(factory, batch, now)
    articles_recovered = recover_article_jobs(factory, batch, now)
    analyses_recovered = recover_analysis_jobs(factory, batch, now)
    topic_analyses_recovered = recover_analysis_jobs(factory, batch, now, model=TopicAnalysisJob)
    notifications_dispatched = notifications_recovered = 0
    if get_settings().notifications_enabled:
        from devfeed_notifications.delivery import recover_notifications
        from devfeed_notifications.dispatcher import dispatch as dispatch_notifications

        notifications_recovered = recover_notifications(factory, batch, now)
        notification_queue = get_queue("notifications")
        try:
            notifications_dispatched = dispatch_notifications(
                factory, notification_queue, batch, utcnow()
            )
        finally:
            notification_queue.connection.close()
    # The PostgreSQL job row is a durable outbox. Publish before stamping dispatch;
    # if we crash between them, re-delivery is safe because claims are exclusive.
    queue = get_queue()
    now = utcnow()  # Include jobs created during the scheduling transaction above.
    try:
        dispatched = dispatch_jobs(factory, queue, batch, now)
        images_dispatched = dispatch_jobs(factory, queue, batch, now, images=True)
        profiles_dispatched = dispatch_jobs(factory, queue, batch, now, profiles=True)
        articles_dispatched = dispatch_jobs(factory, queue, batch, now, articles=True)
        analyses_dispatched = topic_analyses_dispatched = 0
        if get_settings().ai_enabled:
            analysis_queue = get_queue("analysis")
            relationship_queue = get_queue("relationships")
            try:
                analyses_dispatched = dispatch_jobs(
                    factory, analysis_queue, batch, now, analyses=True
                )
                topic_analyses_dispatched = dispatch_jobs(
                    factory, analysis_queue, batch, now, topic_analyses=True, relationships=False
                )
                topic_analyses_dispatched += dispatch_jobs(
                    factory, relationship_queue, batch, now, topic_analyses=True, relationships=True
                )
            finally:
                analysis_queue.connection.close()
                relationship_queue.connection.close()
        queue.connection.set("devfeed:scheduler:heartbeat", now.isoformat(), ex=120)
    finally:
        queue.connection.close()
    return {
        **automation,
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
        "notifications_dispatched": notifications_dispatched,
        "notifications_recovered": notifications_recovered,
    }


def dispatch_jobs(
    factory,
    queue,
    batch,
    now,
    *,
    job_id: uuid.UUID | None = None,
    images=False,
    profiles=False,
    articles=False,
    analyses=False,
    topic_analyses=False,
    relationships: bool | None = None,
):
    if relationships is not None and not topic_analyses:
        raise ValueError("Relationship routing requires topic analysis jobs")
    if sum((images, profiles, articles, analyses, topic_analyses)) > 1:
        raise ValueError("Choose one job type")
    model = (
        TopicAnalysisJob
        if topic_analyses
        else ArticleAnalysisJob
        if analyses
        else ArticleEnrichmentJob
        if articles
        else SourceEnrichmentJob
        if profiles
        else ArticleImageJob
        if images
        else IngestionJob
    )
    dispatched = 0
    for _ in range(batch):
        with factory.begin() as session:
            statement = (
                select(model)
                .where(
                    model.status == "queued",
                    model.available_at <= now,
                    or_(
                        model.dispatched_at.is_(None),
                        model.dispatched_at < now - timedelta(seconds=REDISPATCH_SECONDS),
                    ),
                )
                .order_by(model.available_at)
                .limit(1)
                .with_for_update(skip_locked=job_id is None)
            )
            if job_id is not None:
                statement = statement.where(model.id == job_id)
            if relationships is not None:
                statement = statement.where(
                    TopicAnalysisJob.topic_id.is_not(None)
                    if relationships
                    else TopicAnalysisJob.topic_id.is_(None)
                )
            job = session.scalar(statement)
            if job is None:
                break
            rq_job = queue.enqueue(
                "devfeed_aggregator.topic_analysis_tasks.analyze_topic"
                if topic_analyses
                else "devfeed_aggregator.analysis_tasks.analyze_article"
                if analyses
                else "devfeed_aggregator.article_tasks.enrich_article"
                if articles
                else "devfeed_aggregator.source_tasks.enrich_source"
                if profiles
                else "devfeed_aggregator.image_tasks.enrich_image"
                if images
                else "devfeed_aggregator.tasks.ingest",
                str(job.id),
                job_timeout=240 if topic_analyses else JOB_TIMEOUT_SECONDS,
                result_ttl=0,
                failure_ttl=86400,
                ttl=REDISPATCH_SECONDS,
            )
            job.dispatched_at = now
            dispatched += 1
            fields = {"job_id": job.id, "rq_job_id": rq_job.id}
            if isinstance(job, (ArticleImageJob, ArticleEnrichmentJob, ArticleAnalysisJob)):
                fields["article_id"] = job.article_id
            elif isinstance(job, TopicAnalysisJob):
                fields["topic_id" if job.topic_id else "proposal_id"] = (
                    job.topic_id or job.proposal_id
                )
            else:
                fields["source_id"] = job.source_id
        logger.info(
            "topic_analysis_dispatched"
            if topic_analyses
            else "article_analysis_dispatched"
            if analyses
            else "article_enrichment_dispatched"
            if articles
            else "source_enrichment_dispatched"
            if profiles
            else "image_dispatched"
            if images
            else "ingestion_dispatched",
            extra=fields,
        )
    return dispatched


def recover_analysis_jobs(factory, batch, now, *, model=ArticleAnalysisJob) -> int:
    with factory.begin() as session:
        jobs = session.scalars(
            select(model)
            .where(model.status == "running", model.lease_until < now)
            .order_by(model.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            fail_analysis(job, "worker_lease_expired")
        return len(jobs)


def recover_image_jobs(factory, batch, now) -> int:
    recovered = []
    with factory.begin() as session:
        expired = session.scalars(
            select(ArticleImageJob)
            .where(
                ArticleImageJob.status == "running",
                ArticleImageJob.lease_until < now,
            )
            .order_by(ArticleImageJob.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in expired:
            fail_image(job, "Worker lease expired; interrupted image lookup recovered")
            recovered.append(
                {"job_id": job.id, "article_id": job.article_id, "job_status": job.status}
            )
    for fields in recovered:
        logger.warning("image_lease_recovered", extra=fields)
    return len(recovered)


def recover_source_jobs(factory, batch, now) -> int:
    recovered = []
    with factory.begin() as session:
        expired = session.scalars(
            select(SourceEnrichmentJob)
            .where(
                SourceEnrichmentJob.status == "running",
                SourceEnrichmentJob.lease_until < now,
            )
            .order_by(SourceEnrichmentJob.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in expired:
            fail_enrichment(job, "Worker lease expired; interrupted source enrichment recovered")
            recovered.append(
                {"job_id": job.id, "source_id": job.source_id, "job_status": job.status}
            )
    for fields in recovered:
        logger.warning("source_enrichment_lease_recovered", extra=fields)
    return len(recovered)


def recover_article_jobs(factory, batch, now) -> int:
    recovered = []
    with factory.begin() as session:
        expired = session.scalars(
            select(ArticleEnrichmentJob)
            .where(
                ArticleEnrichmentJob.status == "running",
                ArticleEnrichmentJob.lease_until < now,
            )
            .order_by(ArticleEnrichmentJob.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in expired:
            fail_article(job, "Worker lease expired; interrupted article lookup recovered")
            recovered.append(
                {"job_id": job.id, "article_id": job.article_id, "job_status": job.status}
            )
    for fields in recovered:
        logger.warning("article_enrichment_lease_recovered", extra=fields)
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
