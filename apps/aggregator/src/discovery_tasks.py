"""Background discovery for admin-requested imports; approval is never automatic."""

import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta

from devfeed_core import discovery
from devfeed_core.config import get_settings
from devfeed_core.discovery_quality import DiscoverySampleEntry
from devfeed_core.jobs import REDISPATCH_SECONDS
from devfeed_core.json_types import JsonRecord
from devfeed_core.models import SourceDiscoveryJob, utcnow
from sqlalchemy import Select, case, or_, select, true, tuple_
from sqlalchemy.orm import Session, load_only, sessionmaker

from devfeed_aggregator.queue import get_queue


def assess_sample(sample: list[DiscoverySampleEntry]) -> JsonRecord:
    from devfeed_core.config import get_settings
    from devfeed_core.discovery_quality import Quality, prompt

    from devfeed_aggregator.codex_client import CodexClient

    settings = get_settings()
    if not settings.ai_enabled:
        raise ValueError("Enable AI to run assessment jobs")
    client = CodexClient(settings)
    client.operation = "source_discovery_quality"
    return client.complete(prompt(sample), Quality.model_json_schema())


def process_candidate(job_id: str) -> discovery.DiscoveryRunResult | None:
    return discovery.run_one(
        target_job_id=uuid.UUID(job_id), allow_ai=get_settings().ai_enabled, assessor=assess_sample
    )


class DiscoveryDispatcher:
    """Publish a bounded discovery batch without rechecking an already queued prefix."""

    def __init__(self, factory: sessionmaker[Session], now: datetime) -> None:
        self.factory, self.now = factory, now

    def _statement(self) -> Select[SourceDiscoveryJob]:
        model = SourceDiscoveryJob
        return (
            select(model)
            .where(
                model.result["background"].as_boolean().is_(True),
                model.stage != "assess" if not get_settings().ai_enabled else true(),
                or_(
                    (model.status == "queued") & (model.available_at <= self.now),
                    (model.status == "running") & (model.lease_until < self.now),
                ),
                or_(
                    model.dispatched_at.is_(None),
                    model.dispatched_at < self.now - timedelta(seconds=REDISPATCH_SECONDS),
                    # A newly due retry or explicit request has a later deadline
                    # than its previous publication, so it need not wait for repair.
                    (model.status == "queued") & (model.available_at > model.dispatched_at),
                ),
            )
            .order_by(
                case((model.stage == "assess", 0), else_=1),
                model.available_at,
                model.created_at,
                model.id,
            )
        )

    def publish(self, limit: int) -> int:
        if limit < 1:
            raise ValueError("Discovery publication limit must be positive")
        model = SourceDiscoveryJob
        with self.factory() as session:
            candidates = [
                (identifier, stage)
                for identifier, stage in session.execute(
                    self._statement().with_only_columns(model.id, model.stage).limit(limit)
                )
            ]
        if not get_settings().ai_enabled:
            candidates = [
                (identifier, stage) for identifier, stage in candidates if stage != "assess"
            ]
        if not candidates:
            return 0
        with ExitStack() as stack:
            queues = {}
            for _, stage in candidates:
                name = "source-analysis" if stage == "assess" else "source-discovery"
                if name not in queues:
                    queues[name] = get_queue(name)
                    stack.callback(queues[name].connection.close)
            with self.factory.begin() as session:
                jobs = session.scalars(
                    self._statement()
                    # The queue resources were selected before the row lock.
                    # A changed stage must be admitted through its new queue on
                    # the next pass rather than using a stale route.
                    .where(tuple_(model.id, model.stage).in_(candidates))
                    .options(
                        load_only(
                            model.id,
                            model.stage,
                            model.status,
                            model.available_at,
                            model.lease_until,
                            model.dispatched_at,
                        )
                    )
                    .with_for_update(skip_locked=True)
                ).all()
                for job in jobs:
                    name = "source-analysis" if job.stage == "assess" else "source-discovery"
                    queues[name].enqueue(
                        "devfeed_aggregator.discovery_tasks.process_candidate",
                        str(job.id),
                        job_id=f"source-discovery-{job.id}"
                        if job.stage == "assess"
                        else f"source-discovery-v2-{job.id}",
                        unique=True,
                        job_timeout=300,
                        result_ttl=0,
                        failure_ttl=3600,
                    )
                    job.dispatched_at = self.now
                return len(jobs)


def dispatch_discovery(factory: sessionmaker[Session], limit: int = 10) -> int:
    return DiscoveryDispatcher(factory, utcnow()).publish(limit)
