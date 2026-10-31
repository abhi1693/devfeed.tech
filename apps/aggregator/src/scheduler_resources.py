"""Bound scheduler connection ownership without changing work or dispatch cadence."""

from contextlib import AbstractContextManager, ExitStack
from types import TracebackType

from devfeed_core.cache_events import AppSession
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import create_database_engine
from rq import Queue
from sqlalchemy.orm import Session, sessionmaker

from devfeed_aggregator.queue import get_queue


class SchedulerResources(AbstractContextManager["SchedulerResources"]):
    """Own one bounded SQL pool and one Redis client per main-loop queue.

    Interactive dispatch threads share the thread-safe SQL pool, but continue to
    own their own Redis clients. Workers and APIs keep their existing engines.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.reuse = settings.scheduler_connection_reuse_enabled
        database = (
            settings.model_copy(
                update={
                    "database_pool_enabled": True,
                    "database_pool_size": settings.scheduler_database_pool_size,
                    "database_max_overflow": 0,
                }
            )
            if self.reuse
            else settings
        )
        self.engine = create_database_engine(database)
        self.factory: sessionmaker[Session] = sessionmaker(
            self.engine, class_=AppSession, expire_on_commit=False
        )
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
            self.engine.dispose()
