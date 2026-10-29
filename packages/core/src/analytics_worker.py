"""Import retained history once, then repair bounded current-state observations.

The application publishes live observations directly. This process imports
bounded frozen history, retires PostgreSQL analytics, and reconciles current views.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import time
from datetime import UTC, datetime

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_connections import postgres_connections
from devfeed_core.analytics_import_runner import (
    AnalyticsImportRunner,
    ImportStep,
)
from devfeed_core.analytics_migrations import ClickHouseMigrationRunner
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_reconcile import CurrentStateReconciler
from devfeed_core.analytics_status import AnalyticsStatus
from devfeed_core.config import Settings, get_settings
from devfeed_core.logging import configure_logging
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.telemetry import background_cycle, start_runtime, stop_runtime

logger = logging.getLogger(__name__)
HEALTH_SECONDS = 120
CURRENT_PAGE_SECONDS = 0.1


class AnalyticsImportWorker:
    """Coordinate finite history and bounded repair of current operational views."""

    def __init__(self, client: AnalyticsClient, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self.status = AnalyticsStatus(client)
        self.policy = pipeline_policy_version(self.settings)
        connect = postgres_connections(
            self.settings.database_url,
            connect_timeout=self.settings.database_connect_timeout_seconds,
        )
        self.runner = AnalyticsImportRunner(
            client,
            connect,
            settings=self.settings,
            page_size=self.settings.analytics_backfill_batch_size,
        )
        self.reconciler = CurrentStateReconciler(client, self.settings, connect=connect)
        self.bootstrapping = True
        self._initialized = False
        self._started = False

    async def close(self) -> None:
        await self.reconciler.close()

    async def once(self) -> bool:
        if not self._initialized:
            await ClickHouseMigrationRunner(self.client).check()
            self._initialized = True
        if self.bootstrapping:
            if not self._started:
                try:
                    state = await self.status.read()
                except AnalyticsUnavailable:
                    state = None
                if state and state["ready"] and state["policy"] == self.policy:
                    # Every source, policy-specific card page and cleanup was
                    # durably completed. Restart does not replay historical pages.
                    self.bootstrapping = False
                else:
                    cutover = (
                        state["capture_started_at"]
                        if state
                        else datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                    )
                    await self.status.write(ready=False, cutover=cutover, policy=self.policy)
                self._started = True
            if self.bootstrapping:
                step = await self.runner.once()
                if step is ImportStep.COMPLETE:
                    importer = self.runner._importer
                    if importer is None:
                        raise RuntimeError("Historical import completed without a cutoff")
                    await self.status.write(
                        ready=True, cutover=importer.cutover, policy=self.policy
                    )
                    self.bootstrapping = False
                    logger.info("analytics_history_import_complete")
                await asyncio.to_thread(self._write_heartbeat)
                return step is ImportStep.PROGRESSED
        busy = await self.reconciler.once()
        await asyncio.to_thread(self._write_heartbeat)
        return busy

    @staticmethod
    def _write_heartbeat() -> None:
        write_runtime_marker(runtime_marker("analytics-worker"), str(time.time()))


async def pause(stopping: asyncio.Event, delay: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stopping.wait(), timeout=delay)


def _poll_delay(settings: Settings, *, busy: bool, bootstrapping: bool, failures: int) -> float:
    delay = settings.analytics_poll_seconds
    if busy:
        delay = settings.analytics_backfill_poll_seconds if bootstrapping else CURRENT_PAGE_SECONDS
    return max(delay, min(60, 2 ** min(failures, 6))) if failures else delay


async def run(once: bool = False) -> None:
    settings = get_settings()
    async with AnalyticsClient(settings) as client:
        worker = AnalyticsImportWorker(client, settings)
        failures = 0
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        for item in signals:
            loop.add_signal_handler(item, stopping.set)
        try:
            while not stopping.is_set():
                try:
                    with background_cycle("analytics.history-import"):
                        busy = await worker.once()
                    failures = 0
                except Exception as error:
                    logger.error(
                        "analytics_history_import_failed"
                        if worker.bootstrapping
                        else "analytics_current_reconciliation_failed",
                        extra={"error_type": type(error).__name__},
                    )
                    if once:
                        raise
                    failures += 1
                    busy = False
                if once:
                    return
                await pause(
                    stopping,
                    _poll_delay(
                        settings, busy=busy, bootstrapping=worker.bootstrapping, failures=failures
                    ),
                )
        finally:
            for item in signals:
                loop.remove_signal_handler(item)
            await worker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        marker = runtime_marker("analytics-worker")
        if not marker.is_file() or time.time() - float(marker.read_text()) > HEALTH_SECONDS:
            raise SystemExit(1)
        return
    settings = get_settings()
    configure_logging("analytics-worker", settings.log_level, settings.log_format)
    telemetry = start_runtime("analytics-worker")
    try:
        asyncio.run(run(args.once))
    finally:
        stop_runtime(telemetry)


if __name__ == "__main__":
    main()
