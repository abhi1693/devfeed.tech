"""Common durable outbox publication. The database lock encloses Redis publication."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from devfeed_core.job_definitions import JOB_DEFINITIONS, Job, PipelineKind, SolverJob
from devfeed_core.jobs import REDISPATCH_SECONDS
from devfeed_core.source_relevance import relevance_job_condition

logger = logging.getLogger(__name__)


class QueuedJob(Protocol):
    @property
    def id(self) -> str: ...


class JobQueue(Protocol):
    def enqueue(self, function: str, /, *args, **kwargs) -> QueuedJob: ...


def dispatch_jobs(
    factory: sessionmaker[Session],
    queue: JobQueue,
    batch: int,
    now: datetime,
    *,
    kind: PipelineKind = "ingestion",
    job_id: uuid.UUID | None = None,
    relationships: bool | None = None,
    source_analysis: bool = False,
    solver: bool = False,
) -> int:
    if kind not in JOB_DEFINITIONS:
        raise ValueError("Unknown job type")
    definition = JOB_DEFINITIONS[kind]
    model = definition.model
    lane = definition.lane_condition(relationships)
    if solver and not definition.supports_solver:
        raise ValueError("This job type does not support solver execution")
    if kind == "source-enrichment" and not solver:
        required = relevance_job_condition()
        lane = required if source_analysis else ~required
    if definition.supports_solver:
        solver_lane = cast(type[SolverJob], model).requires_solver.is_(solver)
        lane = and_(lane, solver_lane) if lane is not None else solver_lane
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
            if lane is not None:
                statement = statement.where(lane)
            job = cast(Job | None, session.scalar(statement))
            if job is None:
                break
            delivery = queue.enqueue(
                definition.handler,
                str(job.id),
                job_timeout=max(240, definition.timeout) if solver else definition.timeout,
                result_ttl=0,
                failure_ttl=86400,
                ttl=REDISPATCH_SECONDS,
            )
            job.dispatched_at = now
            fields = definition.log_fields(job)
            # Notifications historically have no RQ identifier in their dispatch event.
            if definition.kind != "notifications":
                fields["rq_job_id"] = delivery.id
        dispatched += 1
        logger.info(definition.event + "_dispatched", extra=fields)
    return dispatched
