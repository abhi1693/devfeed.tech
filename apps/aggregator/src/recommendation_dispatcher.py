"""Keep interactive feed preparation independent of long ingestion scheduler cycles."""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from devfeed_core.recommendations import dispatch_recommendations
from rq import Queue
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


@contextmanager
def recommendation_dispatcher(
    factory: sessionmaker[Session], queue_factory: Callable[[], Queue], *, interval: float = 1.0
) -> Iterator[None]:
    stop = threading.Event()

    def run() -> None:
        queue = None
        try:
            while not stop.is_set():
                started = time.monotonic()
                try:
                    # This loop owns one client/pool. Redis reconnects failed
                    # sockets; idle polls need no new Sentinel/TCP handshake.
                    if queue is None:
                        queue = queue_factory()
                    count = dispatch_recommendations(factory, queue)
                    if count:
                        logger.info(
                            "interactive_recommendations_dispatched",
                            extra={
                                "count": count,
                                "duration_ms": round((time.monotonic() - started) * 1000),
                            },
                        )
                except Exception:
                    # Durable requests remain due for the next bounded pass.
                    logger.exception("interactive_recommendations_dispatch_failed")
                stop.wait(interval)
        finally:
            if queue is not None:
                queue.connection.close()

    thread = threading.Thread(target=run, name="recommendation-dispatch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
