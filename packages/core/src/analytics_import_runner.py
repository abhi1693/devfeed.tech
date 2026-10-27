"""Automatically freeze, import and retire the finite legacy analytics schema."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum

import psycopg
from psycopg.rows import dict_row

from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.analytics_cutover import AnalyticsCutoverBusy, LegacyAnalyticsCutover
from devfeed_core.analytics_import import IMPORT_ID, HistoricalAnalyticsImport

# TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Remove this runner with the one-time
# source import after every supported deployment has completed it.
IMPORT_LOCK_KEY = 0x4446494D


class ImportStep(Enum):
    BUSY = "busy"
    PROGRESSED = "progressed"
    COMPLETE = "complete"


class UnsafeImportCutover(RuntimeError):
    """The retained analytical sources do not have complete write guards."""


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

    Temporary guards reject old analytical writes while source/business tables
    stay writable. Separate connections hold coordination and read source pages;
    no source transaction remains open during a ClickHouse insert.
    """

    def __init__(
        self,
        client,
        connect,
        *,
        page_size=1000,
        timeout=60,
        settings=None,
        cutover=None,
    ):
        if not callable(connect):
            raise ValueError("Source connections are required")
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("Invalid historical import page size")
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValueError("Invalid historical import deadline")
        self.client = client
        self.connect = connect
        self.page_size = page_size
        self.timeout = timeout
        self.settings = settings
        self.cutover = cutover or LegacyAnalyticsCutover()
        self._importer = None

    async def _get_importer(self, connection):
        opens = ArticleOpenAnalytics(self.client)
        cutover, _ = await opens._cutover()
        if self._importer is not None:
            if cutover != self._importer.cutover:
                raise RuntimeError("Historical import cutoff changed or disappeared")
            return self._importer
        if cutover is None:
            # Existing progress without the persisted cutover indicates a
            # partial restore or table loss; choosing a new hour would corrupt
            # lifetime totals. A fresh table with no rows is safe to initialize.
            rows = await self.client.query(
                "SELECT count() AS pages FROM analytics_backfill_progress "
                "WHERE import_id={import_id:String}",
                {"import_id": IMPORT_ID},
            )
            if len(rows) != 1 or type(rows[0].get("pages")) is not int or rows[0]["pages"]:
                raise RuntimeError("Historical import progress exists without its cutoff")
            cutover = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        options = {"settings": self.settings} if self.settings is not None else {}
        importer = HistoricalAnalyticsImport(
            self.client, cutover=cutover, page_size=self.page_size, **options
        )
        await importer.configure(connection)
        await importer.initialize()
        self._importer = importer
        return importer

    async def once(self):
        """Return BUSY, PROGRESSED or COMPLETE without waiting for another runner."""
        async with asyncio.timeout(self.timeout):
            async with self.connect() as lock_connection, lock_connection.transaction():
                locked = await _scalar(
                    lock_connection, "SELECT pg_try_advisory_xact_lock(%s)", (IMPORT_LOCK_KEY,)
                )
                if locked is not True:
                    return ImportStep.BUSY
                async with self.connect() as source_connection:
                    if source_connection is lock_connection:
                        raise RuntimeError("Historical import needs independent source connections")
                    if not await self.cutover.frozen(source_connection):
                        await self.cutover.freeze(source_connection)
                    if not await self.cutover.frozen(source_connection):
                        raise UnsafeImportCutover("Legacy analytics guards are incomplete")
                    # This fails closed if the persisted CH cutoff is ambiguous.
                    importer = await self._get_importer(source_connection)
                    more = await importer.once(source_connection)
                    if not more:
                        await self.cutover.cleanup(source_connection)
                return ImportStep.PROGRESSED if more else ImportStep.COMPLETE

    async def prepare(self):
        """Fix the reader boundary before new producers start, without scanning history."""
        async with asyncio.timeout(self.timeout):
            async with self.connect() as lock_connection, lock_connection.transaction():
                locked = await _scalar(
                    lock_connection, "SELECT pg_try_advisory_xact_lock(%s)", (IMPORT_LOCK_KEY,)
                )
                if locked is not True:
                    raise AnalyticsCutoverBusy("Analytics schema preparation is already running")
                async with self.connect() as source_connection:
                    if source_connection is lock_connection:
                        raise RuntimeError("Historical import needs independent source connections")
                    importer = await self._get_importer(source_connection)
                    return importer.cutover
