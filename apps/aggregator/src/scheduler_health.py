"""Heartbeat long scheduler cycles without hiding a stalled scheduler."""

import logging
import threading
import time
from contextlib import contextmanager

from devfeed_core.config import get_settings
from devfeed_core.models import utcnow
from devfeed_core.redis import create_redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

logger = logging.getLogger(__name__)
HEARTBEAT_KEY = "devfeed:scheduler:heartbeat"
HEARTBEAT_TTL = 120
STALL_SECONDS = 300


class SchedulerHealth:
    def __init__(self, connection, clock=time.monotonic):
        self.connection = connection
        self.clock = clock
        self.last_success = clock()

    def completed(self):
        self.last_success = self.clock()

    def pulse(self):
        # A stuck cycle or repeated failing cycles eventually lose liveness.
        if self.clock() - self.last_success >= STALL_SECONDS:
            return
        try:
            self.connection.set(HEARTBEAT_KEY, utcnow().isoformat(), ex=HEARTBEAT_TTL)
        except RedisError:
            logger.warning("scheduler_heartbeat_unavailable")


@contextmanager
def scheduler_health():
    connection = create_redis(
        get_settings(),
        socket_connect_timeout=1,
        socket_timeout=1,
        retry=Retry(NoBackoff(), retries=0),
    )
    health = SchedulerHealth(connection)
    stop = threading.Event()

    def heartbeat():
        from devfeed_aggregator.quota_monitor import refresh_quota

        quota_checked = float("-inf")
        try:
            while not stop.is_set():
                health.pulse()
                # Long scheduling batches must not age the 180-second quota
                # snapshot out while the provider is healthy. The shared NX
                # gate in refresh_quota bounds polling across processes.
                if health.clock() - quota_checked >= 60:
                    refresh_quota()
                    quota_checked = health.clock()
                stop.wait(20)
        finally:
            connection.close()

    thread = threading.Thread(target=heartbeat, name="scheduler-health", daemon=True)
    thread.start()
    try:
        yield health
    finally:
        stop.set()
        thread.join(timeout=5)
