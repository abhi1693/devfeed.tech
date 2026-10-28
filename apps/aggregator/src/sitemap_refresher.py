"""Generate cached sitemaps independently of crawler requests and ingestion cycles."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from devfeed_core.sitemaps import refresh_snapshot

logger = logging.getLogger(__name__)


@contextmanager
def sitemap_refresher(*, interval: float = 60) -> Iterator[None]:
    stop = threading.Event()

    def run() -> None:
        while not stop.is_set():
            try:
                refresh_snapshot()
            except Exception:
                logger.exception("scheduled_sitemap_refresh_failed")
            stop.wait(interval)

    thread = threading.Thread(target=run, name="sitemap-refresh", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
