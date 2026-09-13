from devfeed_core.config import get_settings
from devfeed_core.jobs import JOB_TIMEOUT_SECONDS
from devfeed_core.redis import create_redis
from devfeed_core.worker_queues import QUEUES
from redis.backoff import ExponentialWithJitterBackoff
from redis.retry import Retry
from rq import Queue
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import JobStatus
from rq.serializers import JSONSerializer


class DurableQueue(Queue):
    """Keep an existing delivery in place when the database outbox checks it again."""

    def enqueue(self, f, *args, **kwargs):
        try:
            return super().enqueue(f, *args, **kwargs)
        except DuplicateJobError:
            if not kwargs.get("unique"):
                raise
            try:
                # Delivery IDs are global. A pre-upgrade delivery may still belong
                # to a legacy queue; Queue.fetch_job hides jobs with another origin.
                existing = self.job_class.fetch(
                    kwargs["job_id"], connection=self.connection, serializer=self.serializer
                )
            except NoSuchJobError:
                existing = None
            if existing is not None:
                status = existing.get_status(refresh=True)
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
            return super().enqueue(f, *args, **kwargs)


def get_queue(name: str = "ingestion") -> Queue:
    if name not in QUEUES:
        raise ValueError("Unknown worker queue")
    return DurableQueue(
        name,
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
