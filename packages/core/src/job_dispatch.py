"""Common durable outbox publication. The database lock encloses Redis publication."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched, groupby
from typing import Protocol, cast

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.config import get_settings
from devfeed_core.delivery import delivery_id
from devfeed_core.job_definitions import (
    JOB_DEFINITIONS,
    Job,
    PipelineKind,
    SolverJob,
    fresh_article_condition,
)
from devfeed_core.jobs import REDISPATCH_SECONDS
from devfeed_core.models import ArticleAnalysisJob, ArticleEnrichmentJob
from devfeed_core.source_relevance import relevance_job_condition

logger = logging.getLogger(__name__)


class QueuedJob(Protocol):
    @property
    def id(self) -> str: ...


class JobQueue(Protocol):
    def enqueue(self, f: str, *args: str, **kwargs: str | int | bool | None) -> QueuedJob: ...


def _lane_condition(
    kind: PipelineKind, relationships: bool | None, source_analysis: bool, solver: bool
) -> ColumnElement[bool] | None:
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
    return lane


@dataclass(frozen=True)
class DispatchLane:
    kind: PipelineKind
    relationships: bool | None = None
    source_analysis: bool = False
    solver: bool = False


def _due_conditions(model: type[Job], now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        model.status == "queued",
        model.available_at <= now,
        or_(
            model.dispatched_at.is_(None),
            model.dispatched_at < now - timedelta(seconds=REDISPATCH_SECONDS),
        ),
    )


class JobDispatcher:
    """Select and publish a bounded batch under one durable transaction.

    Redis publication precedes the database commit. Unique delivery identities
    make a partially published, rolled-back batch safe to replay.
    """

    def __init__(self, factory: sessionmaker[Session], queue: JobQueue, now: datetime) -> None:
        self.factory, self.queue, self.now = factory, queue, now

    def _statement(self, lane: DispatchLane, *, fresh: bool | None = None) -> Select[Job]:
        condition = _lane_condition(
            lane.kind, lane.relationships, lane.source_analysis, lane.solver
        )
        definition = JOB_DEFINITIONS[lane.kind]
        model = definition.model
        if fresh is not None:
            if lane.kind not in {"analysis", "article-enrichment"} or lane.solver:
                raise ValueError("Fresh lanes only support article extraction and analysis")
            article_model = ArticleAnalysisJob if lane.kind == "analysis" else ArticleEnrichmentJob
            recent = fresh_article_condition(article_model)
            recent = recent if fresh else ~recent
            condition = and_(condition, recent) if condition is not None else recent
        statement = (
            select(model)
            .where(*_due_conditions(model, self.now))
            .order_by(model.available_at, model.created_at, model.id)
        )
        # SQLAlchemy infers the common Base for a union of mapped entity classes.
        return cast(Select[Job], statement.where(condition) if condition is not None else statement)

    def _publish(
        self, jobs: Iterable[tuple[PipelineKind, Job, bool, bool]]
    ) -> list[tuple[str, dict[str, uuid.UUID | str]]]:
        logs = []
        for kind, grouped in groupby(jobs, key=lambda row: row[0]):
            rows = list(grouped)
            size = get_settings().notification_delivery_batch_size if kind == "notifications" else 1
            for page in batched(rows, size):
                if len(page) == 1:
                    logs.extend(self._publish_one(*page[0]))
                    continue
                # Durable rows remain individually claimed and retried. This
                # transport grouping only amortizes work-horse startup.
                identifiers = [str(row[1].id) for row in page]
                identities = sorted(
                    delivery_id(kind, row[1].id, row[1].available_at, row[1].attempts)
                    for row in page
                )
                self.queue.enqueue(
                    JOB_DEFINITIONS[kind].handler,
                    json.dumps({"deliveries": identifiers, "published_at": self.now.isoformat()}),
                    job_timeout=30,
                    result_ttl=0,
                    failure_ttl=86400,
                    ttl=None,
                    job_id="devfeed-notifications-batch-"
                    + str(uuid.uuid5(uuid.NAMESPACE_URL, "/".join(identities))),
                    unique=True,
                )
                for _, job, _, _ in page:
                    job.dispatched_at = self.now
                    logs.append(
                        (JOB_DEFINITIONS[kind].event, JOB_DEFINITIONS[kind].log_fields(job))
                    )
        return logs

    def _publish_one(
        self, kind: PipelineKind, job: Job, solver: bool, source_analysis: bool
    ) -> list[tuple[str, dict[str, uuid.UUID | str]]]:
        logs = []
        definition = JOB_DEFINITIONS[kind]
        delivery = self.queue.enqueue(
            definition.handler,
            str(job.id),
            job_timeout=max(240, definition.timeout) if solver else definition.timeout,
            result_ttl=0,
            failure_ttl=86400,
            ttl=None,
            job_id=delivery_id(
                f"{kind}-solver" if solver else f"{kind}-ai" if source_analysis else kind,
                job.id,
                job.available_at,
                job.attempts,
            ),
            unique=True,
        )
        job.dispatched_at = self.now
        fields = definition.log_fields(job)
        if definition.kind != "notifications":
            fields["rq_job_id"] = delivery.id
        logs.append((definition.event, fields))
        return logs

    @staticmethod
    def _log(logs: Iterable[tuple[str, dict[str, uuid.UUID | str]]]) -> None:
        for event, fields in logs:
            logger.info(event + "_dispatched", extra=fields)

    def publish(
        self,
        batch: int,
        lane: DispatchLane,
        *,
        job_id: uuid.UUID | None = None,
        fresh: bool | None = None,
    ) -> int:
        statement = self._statement(lane, fresh=fresh)
        if batch < 1:
            return 0
        model = JOB_DEFINITIONS[lane.kind].model
        if job_id is not None:
            statement = statement.where(model.id == job_id)
        with self.factory.begin() as session:
            jobs = session.scalars(
                statement.options(*JOB_DEFINITIONS[lane.kind].metadata_options())
                .limit(min(batch, 1) if job_id is not None else batch)
                .with_for_update(skip_locked=job_id is None)
            ).all()
            logs = self._publish(
                (lane.kind, job, lane.solver, lane.source_analysis) for job in jobs
            )
        self._log(logs)
        return len(logs)

    def publish_lanes(self, batch: int, lanes: list[DispatchLane]) -> dict[PipelineKind, int]:
        counts = {lane.kind: 0 for lane in lanes}
        candidates: list[tuple[datetime, datetime, uuid.UUID, DispatchLane]] = []
        statements = {lane: self._statement(lane) for lane in lanes}
        if batch < 1:
            return counts
        with self.factory.begin() as session:
            for lane, statement in statements.items():
                model = JOB_DEFINITIONS[lane.kind].model
                candidates.extend(
                    (due, created, identifier, lane)
                    for identifier, due, created in session.execute(
                        statement.with_only_columns(
                            model.id, model.available_at, model.created_at
                        ).limit(batch)
                    )
                )
            # Overlapping lanes must not publish the same durable job twice.
            selected_by_identity: dict[
                tuple[str, uuid.UUID], tuple[datetime, datetime, uuid.UUID, DispatchLane]
            ] = {}
            for row in sorted(candidates, key=lambda row: row[:3]):
                selected_by_identity.setdefault((row[3].kind, row[2]), row)
            selected = list(selected_by_identity.values())[:batch]
            identifiers: dict[DispatchLane, list[uuid.UUID]] = {}
            for _, _, identifier, lane in selected:
                identifiers.setdefault(lane, []).append(identifier)
            locked: dict[tuple[str, uuid.UUID], Job] = {}
            for lane, keys in identifiers.items():
                model = JOB_DEFINITIONS[lane.kind].model
                jobs = session.scalars(
                    statements[lane]
                    .options(*JOB_DEFINITIONS[lane.kind].metadata_options())
                    .where(model.id.in_(keys))
                    .with_for_update(skip_locked=True)
                ).all()
                locked.update(((lane.kind, job.id), job) for job in jobs)
            ready = [
                (lane.kind, locked[(lane.kind, identifier)], lane.solver, lane.source_analysis)
                for _, _, identifier, lane in selected
                if (lane.kind, identifier) in locked
            ]
            logs = self._publish(ready)
            for kind, *_ in ready:
                counts[kind] += 1
        self._log(logs)
        return counts


def dispatch_lanes(
    factory: sessionmaker[Session],
    queue: JobQueue,
    batch: int,
    now: datetime,
    lanes: list[DispatchLane],
) -> dict[PipelineKind, int]:
    """Publish oldest eligible jobs across lanes in one bounded transaction."""
    return JobDispatcher(factory, queue, now).publish_lanes(batch, lanes)


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
    fresh: bool | None = None,
) -> int:
    return JobDispatcher(factory, queue, now).publish(
        batch,
        DispatchLane(kind, relationships, source_analysis, solver),
        job_id=job_id,
        fresh=fresh,
    )
