from devfeed_core.config import get_settings
from devfeed_core.jobs import JOB_TIMEOUT_SECONDS
from redis import Redis
from redis.backoff import ExponentialWithJitterBackoff
from redis.retry import Retry
from rq import Queue
from rq.serializers import JSONSerializer


def get_queue(name: str = "ingestion") -> Queue:
    if name not in {"ingestion", "analysis", "relationships", "notifications"}:
        raise ValueError("Unknown worker queue")
    return Queue(
        name,
        connection=Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=5,
            socket_timeout=5,
            # Redis 8 otherwise expands the previous three retries to ten.
            retry=Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3),
        ),
        serializer=JSONSerializer,
        default_timeout=JOB_TIMEOUT_SECONDS,
    )
