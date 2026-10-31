"""Bound scheduler connection ownership without changing work or dispatch cadence."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from types import TracebackType

from devfeed_core.cache_events import AppSession
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import create_database_engine
from rq import Queue
from sqlalchemy.orm import Session, sessionmaker

from devfeed_aggregator.queue import get_queue


class SchedulerResources(AbstractContextManager["SchedulerResources"]):
    """Reuse SQL within a main-loop cycle and Redis across main-loop cycles.

    When overriding NullPool, main-loop SQL connections close before polling.
    Interactive threads retain their previous connection policy rather than
    pinning idle sessions in the production session-mode PgBouncer pool.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.reuse = settings.scheduler_connection_reuse_enabled
        self._cycle_reuse = self.reuse and not settings.database_pool_enabled
        database = (
            settings.model_copy(
                update={
                    "database_pool_enabled": True,
                    "database_pool_size": settings.scheduler_database_pool_size,
                    "database_max_overflow": 0,
                }
            )
            if self._cycle_reuse
            else settings
        )
        self.engine = create_database_engine(database)
        self.factory: sessionmaker[Session] = sessionmaker(
            self.engine, class_=AppSession, expire_on_commit=False
        )
        self._background_engine = (
            create_database_engine(settings) if self._cycle_reuse else self.engine
        )
        self.background_factory: sessionmaker[Session] = (
            sessionmaker(self._background_engine, class_=AppSession, expire_on_commit=False)
            if self._cycle_reuse
            else self.factory
        )
        self.queues: dict[str, Queue] = {}
        self._connections = ExitStack()

    @contextmanager
    def cycle(self) -> Iterator[None]:
        """Release newly introduced database slots before the polling wait."""
        try:
            yield
        finally:
            if self._cycle_reuse:
                # Only the main thread uses this engine. Sessions retain their
                # independent commits/rollbacks; disposal closes returned sockets
                # without rebuilding the initialized SQLAlchemy dialect.
                self.engine.dispose()

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
            try:
                self.engine.dispose()
            finally:
                if self._background_engine is not self.engine:
                    self._background_engine.dispose()
