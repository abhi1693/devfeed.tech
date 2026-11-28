"""Reuse scheduler Redis clients without changing PostgreSQL ownership."""

from contextlib import AbstractContextManager, ExitStack
from types import TracebackType

from devfeed_core.config import Settings, get_settings
from devfeed_core.db import session_factory
from rq import Queue

from devfeed_aggregator.queue import get_queue


class SchedulerResources(AbstractContextManager["SchedulerResources"]):
    """Own Redis clients while retaining the existing shared SQL factory."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.reuse = settings.scheduler_connection_reuse_enabled
        self.factory = session_factory()
        self.queues: dict[str, Queue] = {}
        self._connections = ExitStack()

    def queue(self, name: str = "ingestion") -> Queue:
        if not self.reuse:
            return get_queue(name)
        if name not in self.queues:
            self.queues[name] = get_queue(name)
            self._connections.callback(self.queues[name].connection.close)
        return self.queues[name]

    def release(self, queue: Queue) -> None:
        if not self.reuse:
            queue.connection.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self._connections.__exit__(exc_type, exc_value, traceback)
        finally:
            self.queues.clear()
