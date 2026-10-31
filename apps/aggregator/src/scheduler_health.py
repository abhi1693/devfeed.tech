"""Heartbeat long scheduler cycles without hiding a stalled scheduler."""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from devfeed_core.config import get_settings
from devfeed_core.models import utcnow
from devfeed_core.redis import create_redis
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from devfeed_aggregator.scheduler_probe import HEARTBEAT_TTL

logger = logging.getLogger(__name__)
HEARTBEAT_KEY = "devfeed:scheduler:heartbeat"
STALL_SECONDS = 300


class SchedulerHealth:
    def __init__(
        self,
        connection: Redis,
        clock: Callable[[], float] = time.monotonic,
        *,
        marker: Path | None = None,
    ) -> None:
        self.connection = connection
        self.clock = clock
        self.last_success = clock()
        self.marker = marker

    def completed(self) -> None:
        self.last_success = self.clock()

    def pulse(self) -> None:
        # A stuck cycle or repeated failing cycles eventually lose liveness.
        if self.clock() - self.last_success >= STALL_SECONDS:
            return
        try:
            self.connection.set(HEARTBEAT_KEY, utcnow().isoformat(), ex=HEARTBEAT_TTL)
        except RedisError:
            logger.warning("scheduler_heartbeat_unavailable")
            if self.marker is not None:
                try:
                    self.marker.unlink(missing_ok=True)
                except OSError:
                    logger.warning("scheduler_heartbeat_marker_unavailable")
            return
        if self.marker is not None:
            try:
                write_runtime_marker(self.marker, str(time.time()))
            except OSError:
                logger.warning("scheduler_heartbeat_marker_unavailable")


@contextmanager
def scheduler_health() -> Iterator[SchedulerHealth]:
    connection = create_redis(
        get_settings(),
        socket_connect_timeout=1,
        socket_timeout=1,
        retry=Retry(NoBackoff(), retries=0),
    )
    marker = runtime_marker("scheduler-heartbeat")
    marker.unlink(missing_ok=True)
    health = SchedulerHealth(connection, marker=marker)
    stop = threading.Event()

    def heartbeat() -> None:
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
        marker.unlink(missing_ok=True)
