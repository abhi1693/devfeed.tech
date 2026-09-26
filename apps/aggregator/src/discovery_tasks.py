"""Background discovery for admin-requested imports; approval is never automatic."""

import uuid

from devfeed_core import discovery
from devfeed_core.config import get_settings
from devfeed_core.models import SourceDiscoveryJob, utcnow
from sqlalchemy import case, or_, select, true

from devfeed_aggregator.queue import get_queue


def assess_sample(sample):
    from devfeed_core.config import get_settings
    from devfeed_core.discovery_quality import Quality, prompt

    from devfeed_aggregator.codex_client import CodexClient

    settings = get_settings()
    if not settings.ai_enabled:
        raise ValueError("Enable AI to run assessment jobs")
    client = CodexClient(settings)
    client.operation = "source_discovery_quality"
    return client.complete(prompt(sample), Quality.model_json_schema())


def process_candidate(job_id: str):
    return discovery.run_one(
        target_job_id=uuid.UUID(job_id), allow_ai=get_settings().ai_enabled, assessor=assess_sample
    )


def dispatch_discovery(factory, limit=10):
    now = utcnow()
    with factory() as session:
        jobs = list(
            session.execute(
                select(SourceDiscoveryJob.id, SourceDiscoveryJob.stage)
                .where(
                    SourceDiscoveryJob.result["background"].as_boolean().is_(True),
                    SourceDiscoveryJob.stage != "assess"
                    if not get_settings().ai_enabled
                    else true(),
                    or_(
                        (SourceDiscoveryJob.status == "queued")
                        & (SourceDiscoveryJob.available_at <= now),
                        (SourceDiscoveryJob.status == "running")
                        & (SourceDiscoveryJob.lease_until < now),
                    ),
                )
                .order_by(
                    case((SourceDiscoveryJob.stage == "assess", 0), else_=1),
                    SourceDiscoveryJob.available_at,
                )
                .limit(limit)
            )
        )
    dispatched = 0
    for job_id, stage in jobs:
        if stage == "assess" and not get_settings().ai_enabled:
            continue
        queue_name = "source-analysis" if stage == "assess" else "source-discovery"
        queue = get_queue(queue_name)
        # Version crawl delivery IDs so old ingestion-queue deliveries can drain.
        delivery_id = (
            f"source-discovery-{job_id}" if stage == "assess" else f"source-discovery-v2-{job_id}"
        )
        try:
            with factory.begin() as session:
                job = session.scalar(
                    select(SourceDiscoveryJob)
                    .where(SourceDiscoveryJob.id == job_id)
                    .with_for_update(skip_locked=True)
                )
                if job is None or (
                    not (job.status == "queued" and job.available_at <= now)
                    and not (job.status == "running" and job.lease_until and job.lease_until < now)
                ):
                    continue
                queue.enqueue(
                    "devfeed_aggregator.discovery_tasks.process_candidate",
                    str(job_id),
                    job_id=delivery_id,
                    unique=True,
                    job_timeout=300,
                    result_ttl=0,
                    failure_ttl=3600,
                )
                job.dispatched_at = now
                dispatched += 1
        finally:
            queue.connection.close()
    return dispatched
