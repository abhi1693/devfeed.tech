import re
from collections.abc import Callable
from typing import Any

from devfeed_core.config import get_settings
from devfeed_core.jobs import JOB_TIMEOUT_SECONDS
from devfeed_core.redis import create_redis
from devfeed_core.telemetry import inject_context
from devfeed_core.worker_queues import ROUTABLE_QUEUES, configured_queue, physical_queues
from redis.backoff import ExponentialWithJitterBackoff
from redis.retry import Retry
from rq import Queue
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

# Check and restore membership atomically. A queued hash alone is not a delivery.
# LMOVE workers temporarily own jobs through the intermediate list; leave them alone.
RESTORE_QUEUED = """
if redis.call('HGET', KEYS[1], 'status') ~= 'queued' then return 0 end
if redis.call('HGET', KEYS[1], 'origin') ~= ARGV[2] then return 0 end
if redis.call('LPOS', KEYS[2], ARGV[1]) then return 0 end
if redis.call('LPOS', KEYS[3], ARGV[1]) then return 0 end
redis.call('SADD', KEYS[4], KEYS[2])
redis.call('RPUSH', KEYS[2], ARGV[1])
return 1
"""


class DurableQueue(Queue):
    """Keep an existing delivery in place when the database outbox checks it again."""

    logical_name: str | None = None

    def enqueue(self, f: str | Callable[..., object], *args: Any, **kwargs: Any) -> Job:
        aliases = {
            "devfeed_aggregator.recommendation_tasks.refresh": "recommendations",
            "devfeed_aggregator.partner_tasks.process_evaluation": "partner-evaluation",
            "devfeed_aggregator.partner_sync_tasks.process_pipeline": "partner-pipeline",
        }
        alias = aliases.get(f) if isinstance(f, str) else None
        if self.logical_name and alias and alias != self.logical_name:
            target = get_queue(alias)
            try:
                return target.enqueue(f, *args, **kwargs)
            finally:
                target.connection.close()
        if self.logical_name:
            kwargs["meta"] = {**(kwargs.get("meta") or {}), "devfeed_queue": self.logical_name}
        if context := inject_context():
            kwargs["meta"] = {**(kwargs.get("meta") or {}), "devfeed_trace": context}
        try:
            # RQ constrains an otherwise independent function TypeVar; both union
            # alternatives are supported, but its stub rejects their union.
            return super().enqueue(f, *args, **kwargs)  # type: ignore[type-var]
        except DuplicateJobError:
            if not kwargs.get("unique"):
                raise
            try:
                # Delivery IDs are global. Queue.fetch_job can hide a job whose
                # origin differs from this queue, so fetch directly by ID.
                existing = self.job_class.fetch(
                    kwargs["job_id"], connection=self.connection, serializer=self.serializer
                )
            except NoSuchJobError:
                existing = None
            if existing is not None:
                status = existing.get_status(refresh=True)
                if status == JobStatus.QUEUED:
                    origin = existing.origin
                    # A routing override may have changed since this delivery was
                    # published. Repair its original transport instead of duplicating it.
                    owned = (existing.meta or {}).get("devfeed_queue") in ROUTABLE_QUEUES
                    if owned and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", origin or ""):
                        key = f"rq:queue:{origin}"
                        self.connection.eval(
                            RESTORE_QUEUED,
                            4,
                            existing.key,
                            key,
                            f"{key}:intermediate",
                            "rq:queues",
                            existing.id,
                            origin,
                        )
                    return existing
                if status not in {
                    JobStatus.FAILED,
                    JobStatus.FINISHED,
                    JobStatus.STOPPED,
                    JobStatus.CANCELED,
                }:
                    return existing
                # A transport failure can happen before the database claim. A
                # terminal delivery must not block the still-due database job.
                existing.delete()
            # The original delivery may have finished and disappeared between
            # the unique enqueue and fetch. DB row locks serialize our publishers.
            return super().enqueue(f, *args, **kwargs)  # type: ignore[type-var]


def get_queue(name: str = "ingestion", *, physical: bool = False) -> DurableQueue:
    settings = get_settings()
    if physical:
        if name not in physical_queues(settings):
            raise ValueError("Unknown worker queue")
        destination = name
    else:
        destination = configured_queue(name, settings)
    queue = DurableQueue(
        destination,
        connection=create_redis(
            get_settings(),
            socket_connect_timeout=5,
            socket_timeout=5,
            # Redis 8 otherwise expands the previous three retries to ten.
            retry=Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3),
        ),
        serializer=JSONSerializer,
        default_timeout=JOB_TIMEOUT_SECONDS,
    )

    queue.logical_name = None if physical else name
    return queue
