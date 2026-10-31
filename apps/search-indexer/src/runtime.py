"""Consume the durable search outbox in its own process, outside the CLI."""

import json
import logging
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from devfeed_core.cache_events import AppSession
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import create_database_engine, session_factory
from devfeed_core.logging import configure_logging
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.search_engine import Typesense
from devfeed_core.search_index import reconcile, sync_batch
from devfeed_core.telemetry import background_cycle, current, start_runtime, stop_runtime
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


@contextmanager
def indexer_database(settings: Settings) -> Iterator[sessionmaker[Session]]:
    """Own one reusable connection for the sequential index consumer."""
    effective = (
        settings.model_copy(
            update={
                "database_pool_enabled": True,
                "database_pool_size": 1,
                "database_max_overflow": 0,
            }
        )
        if settings.search_indexer_connection_reuse_enabled
        else settings
    )
    database = create_database_engine(effective)
    try:
        yield sessionmaker(database, class_=AppSession, expire_on_commit=False)
    finally:
        database.dispose()


def run_indexer(*, once: bool = False, output: Callable[[str], None] = print) -> int:
    settings = get_settings()
    configure_logging("search-indexer", settings.log_level, settings.log_format)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    engine = Typesense(admin=True)
    engine.setup()
    telemetry = start_runtime("search-indexer") if not once else None
    try:
        with indexer_database(settings) as factory:
            return _consume_index(stop, engine, once, output, factory)
    finally:
        stop_runtime(telemetry)


def _consume_index(
    stop: threading.Event,
    engine: Typesense,
    once: bool,
    output: Callable[[str], None],
    factory: sessionmaker[Session] | None = None,
) -> int:
    settings = get_settings()
    factory = factory or session_factory()
    last_reconcile = 0.0
    while not stop.is_set():
        if current() and time.monotonic() >= last_reconcile:
            try:
                with background_cycle("search.reconcile"):
                    reconcile(factory, engine)
            except Exception as exc:
                logger.error(
                    "search_index_reconciliation_failed",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                last_reconcile = time.monotonic() + settings.search_reconcile_interval_seconds
        try:
            with background_cycle("search.sync"):
                count = sync_batch(factory, engine)
        except Exception as exc:
            logger.error("search_index_sync_failed", extra={"error_type": type(exc).__name__})
            if once:
                return 1
            stop.wait(5)
        else:
            last_heartbeat = time.time()
            write_runtime_marker(runtime_marker("search-heartbeat"), str(last_heartbeat))
            if once:
                output(json.dumps({"processed": count}))
                return 0
            if not count:
                stop.wait(1)
    return 0
