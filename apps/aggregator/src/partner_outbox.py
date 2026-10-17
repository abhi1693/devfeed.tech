"""Publish bounded partner work without overfilling shared worker queues."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from devfeed_core.models import PartnerEvaluation, PartnerPipelineJob
from rq import Queue
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

Job = TypeVar("Job", PartnerPipelineJob, PartnerEvaluation)
_QUEUE_LOCKS = {"ingestion": 734819026, "source-analysis": 734819027}


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
    ):
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
    ) -> int:
        if queue_name not in _QUEUE_LOCKS:
            raise ValueError("Unsupported partner worker queue")
        requested = self.batch_size if limit is None else limit
        if requested < 1:
            raise ValueError("Partner publication limit must be positive")
        queue = None
        dispatch_logs = []
        try:
            with self.factory.begin() as session:
                if not session.scalar(
                    select(func.pg_try_advisory_xact_lock(_QUEUE_LOCKS[queue_name]))
                ):
                    return 0
                queue = self.get_queue(queue_name)
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
                if remaining == 1 and fresh and recovery:
                    turn = queue.connection.incr(
                        f"devfeed:partner-dispatch:recovery-turn:{queue_name}:{model.__tablename__}"
                    )
                    jobs = recovery if turn % 4 == 0 else fresh
                else:
                    jobs = fresh[: remaining - len(recovery)] + recovery
                for job in jobs:
                    queue.enqueue(
                        handler,
                        str(job.id),
                        job_id=delivery_id(job),
                        unique=True,
                        job_timeout=300,
                        result_ttl=0,
                        failure_ttl=60,
                    )
                    job.dispatched_at = self.now
                    dispatch_logs.append({**fields(job), "queue": queue_name})
        finally:
            if queue is not None:
                queue.connection.close()
        for entry in dispatch_logs:
            logger.info(event, extra=entry)
        return len(dispatch_logs)
