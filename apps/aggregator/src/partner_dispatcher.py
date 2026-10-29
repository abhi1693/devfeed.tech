"""Refill partner workers independently of long ingestion scheduler cycles."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from devfeed_aggregator.partner_sync_tasks import dispatch_partner_pipeline
from devfeed_aggregator.partner_tasks import dispatch_partner_evaluations

logger = logging.getLogger(__name__)


@contextmanager
def partner_dispatcher(factory: sessionmaker[Session], *, interval: float = 1.0) -> Iterator[None]:
    stop = threading.Event()

    def run() -> None:
        dispatches = (
            ("pipeline", lambda: dispatch_partner_pipeline(factory, schedule=False)),
            ("evaluation", lambda: dispatch_partner_evaluations(factory)),
        )
        while not stop.is_set():
            for operation, dispatch in dispatches:
                if stop.is_set():
                    break
                try:
                    count = dispatch()
                    if count:
                        logger.info("partner_" + operation + "_refilled", extra={"count": count})
                except Exception as exc:
                    # The other lane can progress; PostgreSQL retains failed publication.
                    logger.warning(
                        "partner_" + operation + "_refill_failed",
                        extra={"error_type": type(exc).__name__},
                    )
            # The operations may share a physical queue. Rotate first access.
            dispatches = dispatches[::-1]
            stop.wait(interval)

    thread = threading.Thread(target=run, name="partner-dispatch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
