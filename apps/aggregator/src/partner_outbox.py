"""Publish bounded partner work without overfilling shared worker queues."""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, TypeVar

from devfeed_core.models import PartnerEvaluation, PartnerPipelineJob
from rq import Queue
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

Job = TypeVar("Job", PartnerPipelineJob, PartnerEvaluation)
_LANES = {"ingestion", "source-analysis"}
DispatchLog = tuple[str, dict[str, Any]]
PUBLICATION_BATCH_SECONDS = 1.0


def emit_dispatch_logs(records: list[DispatchLog]) -> None:
    for event, fields in records:
        logger.info(event, extra=fields)


@contextmanager
def _publication_session(
    factory: sessionmaker[Session], session: Session | None
) -> Iterator[Session]:
    if session is None:
        with factory.begin() as current:
            yield current
    else:
        with session.begin_nested():
            yield session


class PartnerOutbox:
    """Refill each queue under a shared, nonblocking publication lock.

    Publication precedes the database claim. DurableQueue's unique delivery IDs
    make a rolled-back batch safe to publish again after a transport failure.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        get_queue: Callable[[str], Queue],
        *,
        batch_size: int,
        now: datetime,
    ) -> None:
        if batch_size < 1:
            raise ValueError("Partner publication batch size must be positive")
        self.factory = factory
        self.get_queue = get_queue
        self.batch_size = batch_size
        self.now = now

    def publish(
        self,
        queue_name: str,
        statement: Select[Job],
        *,
        model: type[Job],
        handler: str,
        delivery_id: Callable[[Job], str],
        event: str,
        fields: Callable[[Job], dict[str, Any]],
        limit: int | None = None,
        session: Session | None = None,
        pending_logs: list[DispatchLog] | None = None,
        deadline: float | None = None,
    ) -> int:
        if queue_name not in _LANES:
            raise ValueError("Unsupported partner worker queue")
        requested = self.batch_size if limit is None else limit
        if requested < 1:
            raise ValueError("Partner publication limit must be positive")
        deadline = time.monotonic() + PUBLICATION_BATCH_SECONDS if deadline is None else deadline
        queue = None
        dispatch_logs: list[dict[str, Any]] = []
        try:
            with _publication_session(self.factory, session) as session:
                # Empty partner lanes need no Redis connection, queue lock or
                # two claim queries. Claims are still rechecked under the lock.
                if not session.scalar(select(statement.order_by(None).exists())):
                    return 0
                logical = "partner-evaluation" if model is PartnerEvaluation else "partner-pipeline"
                queue = self.get_queue(logical)
                # Different job kinds and pipeline stages can share one physical
                # queue. Serialize its capacity check and publication together.
                if not session.scalar(
                    select(
                        func.pg_try_advisory_xact_lock(
                            func.hashtextextended("devfeed:partner-outbox:" + queue.name, 0)
                        )
                    )
                ):
                    return 0
                remaining = min(requested, self.batch_size * 2 - queue.count)
                if remaining <= 0:
                    return 0
                fresh = list(
                    session.scalars(
                        statement.where(model.dispatched_at.is_(None))
                        .limit(remaining)
                        .with_for_update(skip_locked=True)
                    ).all()
                )
                # Reserve recovery capacity so an uninterrupted new import cannot
                # strand previously published jobs after a transport/worker loss.
                recovery_limit = (
                    max(remaining - len(fresh), max(1, remaining // 4)) if fresh else remaining
                )
                recovery = list(
                    session.scalars(
                        statement.where(model.dispatched_at.is_not(None))
                        .limit(recovery_limit)
                        .with_for_update(skip_locked=True)
                    ).all()
                )
                turn_key = None
                turn = 0
                if fresh and recovery:
                    turn_key = (
                        f"devfeed:partner-dispatch:recovery-turn:{queue_name}:{model.__tablename__}"
                    )
                    turn = int(queue.connection.get(turn_key) or 0) + 1
                if remaining == 1 and fresh and recovery:
                    jobs = recovery if turn % 4 == 0 else fresh
                else:
                    selected_fresh = fresh[: remaining - len(recovery)]
                    jobs = (
                        recovery + selected_fresh
                        if turn_key and turn % 4 == 0
                        else selected_fresh + recovery
                    )
                for job in jobs:
                    # A successful but slow response must not extend this batch
                    # by another enqueue. One attempt per eligible lane still
                    # makes progress when prechecks consume the shared budget.
                    if dispatch_logs and time.monotonic() >= deadline:
                        break
                    queue.enqueue(
                        handler,
                        str(job.id),
                        job_id=delivery_id(job),
                        unique=True,
                        job_timeout=300,
                        result_ttl=0,
                        failure_ttl=60,
                        meta={"devfeed_ai": queue_name == "source-analysis"},
                    )
                    if turn_key and not dispatch_logs:
                        # Full/no-op/failed admissions must not consume a turn.
                        queue.connection.incr(turn_key)
                    job.dispatched_at = self.now
                    dispatch_logs.append({**fields(job), "queue": queue_name})
        finally:
            if queue is not None:
                queue.connection.close()
        records = [(event, entry) for entry in dispatch_logs]
        if pending_logs is not None:
            pending_logs.extend(records)
        else:
            emit_dispatch_logs(records)
        return len(dispatch_logs)
