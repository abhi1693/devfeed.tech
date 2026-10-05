"""Consume the durable search outbox in its own process, outside the CLI."""

import json
import logging
import signal
import threading
import time
from collections.abc import Callable

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.logging import configure_logging
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.search_engine import Typesense
from devfeed_core.search_index import reconcile, sync_batch
from devfeed_core.telemetry import background_cycle, current, start_runtime, stop_runtime

logger = logging.getLogger(__name__)


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
        return _consume_index(stop, engine, once, output)
    finally:
        stop_runtime(telemetry)


def _consume_index(stop, engine, once: bool, output: Callable[[str], None]) -> int:
    settings = get_settings()
    last_reconcile = 0.0
    while not stop.is_set():
        if current() and time.monotonic() >= last_reconcile:
            try:
                with background_cycle("search.reconcile"):
                    reconcile(session_factory(), engine)
            except Exception as exc:
                logger.error(
                    "search_index_reconciliation_failed",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                last_reconcile = time.monotonic() + settings.search_reconcile_interval_seconds
        try:
            with background_cycle("search.sync"):
                count = sync_batch(session_factory(), engine)
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
