"""Run the finite direct-analytics history import after legacy writers drain.

This runner is intentionally not attached to a service. A deployment must first
verify that all old analytical writers have stopped, including in-flight work.
The import reads bounded pages and stores its cutoff and progress in ClickHouse.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum

import psycopg
from psycopg.rows import dict_row

from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.analytics_backfill import BACKFILL_DDL
from devfeed_core.analytics_import import IMPORT_ID, HistoricalAnalyticsImport

# TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Remove this runner with the one-time
# source import after every supported deployment has completed it.
IMPORT_LOCK_KEY = 0x4446494D


class ImportStep(Enum):
    BUSY = "busy"
    PROGRESSED = "progressed"
    COMPLETE = "complete"


class UnsafeImportCutover(RuntimeError):
    """The old analytical writers are not proven to have drained."""


def postgres_connections(database_url, *, connect_timeout=5):
    """Return independent async connections for the import lock and source reads."""
    if not isinstance(database_url, str) or not database_url:
        raise ValueError("An import database URL is required")
    if type(connect_timeout) is not int or not 1 <= connect_timeout <= 60:
        raise ValueError("Invalid PostgreSQL connect timeout")
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, row_factory=dict_row, connect_timeout=connect_timeout
        ) as connection:
            yield connection

    return connect


async def _scalar(connection, statement, parameters=None):
    row = await (await connection.execute(statement, parameters)).fetchone()
    if row is None:
        raise RuntimeError("Historical import coordination failed")
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


class AnalyticsImportRunner:
    """Process one bounded page while holding a separate advisory lock.

    The verifier must establish that pre-cutover analytics writers and their
    in-flight transactions have drained. It must continue to hold for the whole
    import. The two connection contexts must point to the same primary database.
    If a lock is lost during a ClickHouse write, frozen source pages and stable
    target identities make replay safe; a conflicting cutoff fails closed.
    """

    def __init__(self, client, connect, legacy_writers_drained, *, page_size=1000, timeout=60):
        if not callable(connect) or not callable(legacy_writers_drained):
            raise ValueError("Source connections and a legacy-writer drain check are required")
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("Invalid historical import page size")
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValueError("Invalid historical import deadline")
        self.client = client
        self.connect = connect
        self.legacy_writers_drained = legacy_writers_drained
        self.page_size = page_size
        self.timeout = timeout
        self._importer = None

    async def _get_importer(self):
        opens = ArticleOpenAnalytics(self.client)
        if self._importer is None:
            await opens.initialize()
        cutover, _ = await opens._cutover()
        if self._importer is not None:
            if cutover != self._importer.cutover:
                raise RuntimeError("Historical import cutoff changed or disappeared")
            return self._importer
        if cutover is None:
            # Existing progress without the persisted cutover indicates a
            # partial restore or table loss; choosing a new hour would corrupt
            # lifetime totals. A fresh table with no rows is safe to initialize.
            await self.client.execute(BACKFILL_DDL)
            rows = await self.client.query(
                "SELECT count() AS pages FROM analytics_backfill_progress "
                "WHERE import_id={import_id:String}",
                {"import_id": IMPORT_ID},
            )
            if len(rows) != 1 or type(rows[0].get("pages")) is not int or rows[0]["pages"]:
                raise RuntimeError("Historical import progress exists without its cutoff")
            cutover = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        importer = HistoricalAnalyticsImport(self.client, cutover=cutover, page_size=self.page_size)
        await importer.initialize()
        self._importer = importer
        return importer

    async def once(self):
        """Return BUSY, PROGRESSED or COMPLETE without waiting for another runner."""
        async with asyncio.timeout(self.timeout):
            if not await self.legacy_writers_drained():
                raise UnsafeImportCutover("Legacy analytics writers have not drained")
            async with self.connect() as lock_connection, lock_connection.transaction():
                locked = await _scalar(
                    lock_connection, "SELECT pg_try_advisory_xact_lock(%s)", (IMPORT_LOCK_KEY,)
                )
                if locked is not True:
                    return ImportStep.BUSY
                # This read fails closed if the ClickHouse cutoff is ambiguous.
                importer = await self._get_importer()
                async with self.connect() as source_connection:
                    if source_connection is lock_connection:
                        raise RuntimeError("Historical import needs independent source connections")
                    more = await importer.once(source_connection)
                return ImportStep.PROGRESSED if more else ImportStep.COMPLETE
