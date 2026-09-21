"""Keep interactive feed preparation independent of long ingestion scheduler cycles."""

import logging
import threading
import time
from contextlib import contextmanager

from devfeed_core.recommendations import dispatch_recommendations

logger = logging.getLogger(__name__)


@contextmanager
def recommendation_dispatcher(factory, queue_factory, *, interval=1.0):
    stop = threading.Event()

    def run():
        while not stop.is_set():
            started = time.monotonic()
            queue = None
            try:
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
                # PostgreSQL retains due work; the main scheduler is also a fallback.
                logger.exception("interactive_recommendations_dispatch_failed")
            finally:
                if queue is not None:
                    queue.connection.close()
            stop.wait(interval)

    thread = threading.Thread(target=run, name="recommendation-dispatch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
