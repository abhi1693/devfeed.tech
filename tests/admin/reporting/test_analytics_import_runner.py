"""One-time imports retain their cutoff and never run against undrained writers."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from devfeed_core import analytics_import_runner as runner


class Cursor:
    def __init__(self, value):
        self.value = value

    async def fetchone(self):
        return {"locked": self.value}


class Connection:
    def __init__(self, pool):
        self.pool = pool
        self.owns_lock = False

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        finally:
            if self.owns_lock:
                self.pool.lock.release()
                self.owns_lock = False

    async def execute(self, statement, _parameters):
        assert statement == "SELECT pg_try_advisory_xact_lock(%s)"
        if self.pool.lock.locked():
            return Cursor(False)
        await self.pool.lock.acquire()
        self.owns_lock = True
        return Cursor(True)


class Pool:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.connections = 0

    @asynccontextmanager
    async def connect(self):
        self.connections += 1
        yield Connection(self)


class Store:
    def __init__(self):
        self.cutover = None
        self.pages = 0
        self.resume = None
        self.pause = None
        self.inflight = None
        self.fail = False
        self.progress = 0

    async def execute(self, _statement):
        pass

    async def query(self, _statement, _parameters):
        return [{"pages": self.progress}]


class Opens:
    def __init__(self, client):
        self.client = client

    async def initialize(self):
        pass

    async def _cutover(self):
        return self.client.cutover, False


class Importer:
    def __init__(self, client, *, cutover, page_size):
        self.client = client
        self.cutover = cutover
        assert 1 <= page_size <= 1000

    async def configure(self, _source):
        pass

    async def initialize(self):
        if self.client.cutover is None:
            self.client.cutover = self.cutover
        assert self.cutover == self.client.cutover

    async def once(self, _source):
        if self.client.inflight:
            self.client.inflight.set()
        if self.client.pause:
            await self.client.pause.wait()
        if self.client.fail:
            raise RuntimeError("insertion failed")
        if self.client.pages >= 2:
            return False
        self.client.pages += 1
        return True


@pytest.fixture(autouse=True)
def replace_import_components(monkeypatch, request):
    if request.node.get_closest_marker("integration"):
        return
    monkeypatch.setattr(runner, "ArticleOpenAnalytics", Opens)
    monkeypatch.setattr(runner, "HistoricalAnalyticsImport", Importer)
    monkeypatch.setattr(
        runner,
        "LegacyAnalyticsCutover",
        lambda: SimpleNamespace(
            frozen=AsyncMock(return_value=True), freeze=AsyncMock(), cleanup=AsyncMock()
        ),
    )


def test_restart_reuses_cutover_and_one_page_per_call():
    async def run():
        pool, store = Pool(), Store()
        first = runner.AnalyticsImportRunner(store, pool.connect, page_size=2)
        assert await first.once() is runner.ImportStep.PROGRESSED
        cutoff = store.cutover
        assert isinstance(cutoff, datetime) and cutoff.tzinfo == UTC
        second = runner.AnalyticsImportRunner(store, pool.connect, page_size=2)
        assert await second.once() is runner.ImportStep.PROGRESSED
        assert store.cutover == cutoff
        assert await second.once() is runner.ImportStep.COMPLETE
        assert pool.connections == 6

    asyncio.run(run())


def test_schema_preparation_fixes_boundary_without_processing_history():
    async def run():
        pool, store = Pool(), Store()
        first = runner.AnalyticsImportRunner(store, pool.connect)
        cutoff = await first.prepare()
        assert store.pages == 0 and store.cutover == cutoff
        assert await runner.AnalyticsImportRunner(store, pool.connect).prepare() == cutoff
        store.cutover = None
        store.progress = 1
        with pytest.raises(RuntimeError, match="progress exists without its cutoff"):
            await runner.AnalyticsImportRunner(store, pool.connect).prepare()

    asyncio.run(run())


def test_concurrent_runner_returns_busy_and_never_reads_source():
    async def run():
        pool, store = Pool(), Store()
        store.pause, store.inflight = asyncio.Event(), asyncio.Event()
        first = runner.AnalyticsImportRunner(store, pool.connect)
        active = asyncio.create_task(first.once())
        await store.inflight.wait()
        other = runner.AnalyticsImportRunner(store, pool.connect)
        assert await other.once() is runner.ImportStep.BUSY
        assert pool.connections == 3
        store.pause.set()
        assert await active is runner.ImportStep.PROGRESSED
        assert store.pages == 1

    asyncio.run(run())


def test_incomplete_guards_or_failed_import_does_not_advance():
    async def run():
        pool, store = Pool(), Store()

        incomplete = SimpleNamespace(
            frozen=AsyncMock(return_value=False), freeze=AsyncMock(), cleanup=AsyncMock()
        )
        unsafe = runner.AnalyticsImportRunner(store, pool.connect, cutover=incomplete)
        with pytest.raises(runner.UnsafeImportCutover):
            await unsafe.once()
        assert pool.connections == 2 and store.cutover is None
        store.fail = True
        safe = runner.AnalyticsImportRunner(store, pool.connect)
        with pytest.raises(RuntimeError, match="insertion failed"):
            await safe.once()
        assert store.pages == 0 and not pool.lock.locked()
        cutoff = store.cutover
        store.fail = False
        assert await safe.once() is runner.ImportStep.PROGRESSED
        assert store.cutover == cutoff

    asyncio.run(run())


def test_missing_cutoff_with_existing_progress_fails_closed():
    async def run():
        pool, store = Pool(), Store()
        store.progress = 1
        importer = runner.AnalyticsImportRunner(store, pool.connect)
        with pytest.raises(RuntimeError, match="progress exists without its cutoff"):
            await importer.once()
        assert store.cutover is None and store.pages == 0
        assert not pool.lock.locked()

    asyncio.run(run())


def test_invalid_setup_fails_immediately():
    with pytest.raises(ValueError):
        runner.AnalyticsImportRunner(None, None)
    with pytest.raises(ValueError):
        runner.AnalyticsImportRunner(None, Pool().connect, page_size=1001)
    with pytest.raises(ValueError):
        runner.postgres_connections("", connect_timeout=5)


def test_deadline_releases_the_lock_when_an_insert_stalls():
    async def run():
        pool, store = Pool(), Store()
        store.pause = asyncio.Event()
        importer = runner.AnalyticsImportRunner(store, pool.connect, timeout=0.02)
        with pytest.raises(TimeoutError):
            await importer.once()
        assert not pool.lock.locked() and store.pages == 0
        cutoff = store.cutover
        store.pause.set()
        assert await importer.once() is runner.ImportStep.PROGRESSED
        assert store.cutover == cutoff

    asyncio.run(run())


@pytest.mark.integration
def test_real_runner_resumes_cutover_and_rejects_partial_loss(database):
    """Use only disposable test databases and never an existing app volume."""
    import os
    import uuid

    import psycopg
    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.config import get_settings
    from pydantic import SecretStr

    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for ClickHouse integration")
    db_name = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not db_name.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    settings = get_settings().model_copy(
        update={
            "clickhouse_url": url,
            "clickhouse_database": db_name,
            "clickhouse_username": os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
            "clickhouse_password": SecretStr(
                os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", "")
            ),
        }
    )

    async def run():
        target = f"direct_runner_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    connect = runner.postgres_connections(settings.database_url)
                    first = runner.AnalyticsImportRunner(client, connect, page_size=2)
                    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
                    async with (
                        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection,
                        connection.transaction(),
                    ):
                        await connection.execute(
                            "SELECT pg_advisory_xact_lock(%s)", (runner.IMPORT_LOCK_KEY,)
                        )
                        assert await first.once() is runner.ImportStep.BUSY
                    assert await first.once() is runner.ImportStep.PROGRESSED
                    cutoff = first._importer.cutover
                    resumed = runner.AnalyticsImportRunner(client, connect, page_size=2)
                    for _ in range(50):
                        if await resumed.once() is runner.ImportStep.COMPLETE:
                            break
                    else:
                        pytest.fail("Historical import failed to finish")
                    assert resumed._importer.cutover == cutoff
                    await client.execute("TRUNCATE TABLE analytics_article_open_cutover")
                    fresh = runner.AnalyticsImportRunner(client, connect, page_size=2)
                    with pytest.raises(RuntimeError, match="progress exists without its cutoff"):
                        await fresh.once()
            finally:
                await admin.execute(f"DROP DATABASE `{target}`")

    asyncio.run(run())


@pytest.mark.integration
def test_automatic_worker_imports_history_retires_postgres_and_restarts_without_scan(
    integration_environment, monkeypatch
):
    import uuid
    from datetime import timedelta

    from alembic import command
    from alembic.config import Config
    from devfeed_core import analytics_producer
    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
    from devfeed_core.analytics_cutover import LegacyAnalyticsCutover
    from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
    from devfeed_core.analytics_inference_store import InferenceStore
    from devfeed_core.analytics_worker import AnalyticsImportWorker
    from devfeed_core.config import get_settings
    from devfeed_core.models import Article
    from sqlalchemy import text

    from tests.support import analytics
    from tests.support.legacy_migrations import database_at_revision
    from tests.support.paths import REPO_ROOT

    configured = analytics.settings()
    monkeypatch.setattr(analytics_producer, "submit", lambda changes: None)
    with database_at_revision(monkeypatch, "0037") as database:
        article_id, call_id = uuid.uuid4(), uuid.uuid4()
        happened = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
        with database.begin() as session:
            session.add(
                Article(
                    id=article_id,
                    title="Historical analytics",
                    url_hash=article_id.hex,
                    canonical_url=f"https://import.invalid/{article_id}",
                )
            )
        with database.begin() as session:
            session.execute(
                text("INSERT INTO article_engagement(article_id,opens) VALUES (:id,12)"),
                {"id": article_id},
            )
            session.execute(
                text(
                    "INSERT INTO article_opens(article_id,viewer_key,opened_hour) "
                    "VALUES (:id,'" + "f" * 64 + "',:at)"
                ),
                {"id": article_id, "at": happened},
            )
            session.execute(
                text(
                    "INSERT INTO inference_calls(id,started_at,finished_at,operation,request_hash,"
                    "status,tokens,web_searches,duration_ms) "
                    "VALUES (:id,:at,:at,'historical','fixture','succeeded','{}'::jsonb,0,12)"
                ),
                {"id": call_id, "at": happened},
            )
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
        configured = configured.model_copy(
            update={
                "database_url": get_settings().database_url,
                "analytics_backfill_batch_size": 2,
            }
        )

        async def run():
            target = "automatic_import_" + uuid.uuid4().hex + "_test"
            async with AnalyticsClient(
                configured.model_copy(update={"clickhouse_database": "default"})
            ) as admin:
                await admin.execute(f"CREATE DATABASE `{target}`")
                try:
                    async with AnalyticsClient(
                        configured.model_copy(update={"clickhouse_database": target})
                    ) as client:
                        client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                        first = AnalyticsImportWorker(client, client.settings)
                        await client.initialize()
                        prepared_cutoff = await first.runner.prepare()
                        opens = ArticleOpenAnalytics(client)
                        assert await opens.write_cutover() == prepared_cutoff
                        await opens.record(article_id, "a" * 64, datetime.now(UTC))
                        for _ in range(120):
                            await first.once()
                            if not first.bootstrapping:
                                break
                        else:
                            pytest.fail("Automatic import did not complete")
                        state = await client.status()
                        assert state["ready"]
                        cutoff = await opens.cutover()
                        assert (await opens.totals([str(article_id)], cutover=cutoff))[
                            str(article_id)
                        ] == 13
                        assert await InferenceStore(client).exists(call_id, happened)
                        async with first.runner.connect() as connection:
                            assert not await LegacyAnalyticsCutover().tables(connection)
                            row = await (
                                await connection.execute(
                                    "SELECT title FROM articles WHERE id=%s", (article_id,)
                                )
                            ).fetchone()
                            assert row["title"] == "Historical analytics"
                        restarted = AnalyticsImportWorker(client, client.settings)

                        async def unexpected_source_read():
                            raise AssertionError("Ready restart read historical PostgreSQL")

                        restarted.runner.once = unexpected_source_read
                        assert await restarted.once() is False
                        assert not restarted.bootstrapping
                        assert not await DirectEngagementAnalytics(client).search_counts([])
                finally:
                    await admin.execute(f"DROP DATABASE `{target}` SYNC")

        asyncio.run(run())
