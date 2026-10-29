"""Immutable ClickHouse revisions recover from partial DDL without false acknowledgements."""

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from devfeed_core import (
    analytics_connections,
    analytics_import_runner,
    analytics_migrations,
    config,
)
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.analytics_migrations import (
    HISTORY_TABLE,
    SCHEMA_LOCK_KEY,
    AnalyticsMigrationError,
    ClickHouseMigrationRunner,
    Migration,
    MigrationStatus,
)

from tests.support.analytics import settings as integration_settings


def revisions():
    return (
        Migration(1, "0001_history.sql", "CREATE TABLE IF NOT EXISTS analytics_schema_migrations"),
        Migration(
            2,
            "0002_events.sql",
            "CREATE TABLE IF NOT EXISTS analytics_events;\n-- statement\n"
            "ALTER TABLE analytics_events ADD COLUMN IF NOT EXISTS count UInt64;",
        ),
    )


def acknowledgement(migration):
    return {
        "version": migration.version,
        "name": migration.name,
        "checksum": migration.checksum,
    }


class JournalClient:
    """Model acknowledged server state separately from interrupted client responses."""

    def __init__(self, *, exists=False, rows=()):
        self.exists = exists
        self.rows = list(rows)
        self.statements = []
        self.inserts = []
        self.fail_statement = None
        self.fail_acknowledgement = None

    async def query(self, statement):
        if statement.startswith("EXISTS TABLE"):
            assert statement == "EXISTS TABLE " + HISTORY_TABLE
            return [{"result": int(self.exists)}]
        assert (
            statement == "SELECT version,name,checksum FROM " + HISTORY_TABLE + " ORDER BY version"
        )
        return list(self.rows)

    async def execute(self, statement):
        self.statements.append(statement)
        if statement == self.fail_statement:
            self.fail_statement = None
            raise AnalyticsUnavailable("DDL interrupted")
        if statement.startswith("CREATE TABLE IF NOT EXISTS " + HISTORY_TABLE):
            self.exists = True

    async def insert(self, table, rows):
        assert table == HISTORY_TABLE and self.exists
        self.inserts.append(rows)
        failure = self.fail_acknowledgement
        self.fail_acknowledgement = None
        if failure != "before":
            self.rows.extend(rows)
        if failure is not None:
            raise AnalyticsUnavailable("Acknowledgement interrupted")


def test_migration_checksum_is_exact_utf8_and_statement_boundaries_preserve_sql_literals():
    sql = "SELECT 'snowman ☃;still one statement';\n-- statement\nSELECT 2;\n"
    migration = Migration(1, "0001_literals.sql", sql)
    assert migration.checksum == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert migration.checksum != Migration(1, migration.name, sql.rstrip()).checksum
    assert migration.statements == ("SELECT 'snowman ☃;still one statement'", "SELECT 2")
    assert Migration(1, "inline.sql", "SELECT '-- statement';").statements == (
        "SELECT '-- statement'",
    )


def test_resource_loader_sorts_sql_revisions_and_ignores_package_metadata(tmp_path, monkeypatch):
    (tmp_path / "0002_events.sql").write_text("SELECT 'é';", encoding="utf-8")
    (tmp_path / "0001_history.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(analytics_migrations, "files", lambda package: tmp_path)
    assert analytics_migrations.load_migrations() == (
        Migration(1, "0001_history.sql", "SELECT 1;"),
        Migration(2, "0002_events.sql", "SELECT 'é';"),
    )


@pytest.mark.parametrize("filename", ["1_initial.sql", "0001_Initial.sql", "0001_bad-name.sql"])
def test_resource_loader_rejects_ambiguous_sql_names(tmp_path, monkeypatch, filename):
    (tmp_path / filename).write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(analytics_migrations, "files", lambda package: tmp_path)
    with pytest.raises(AnalyticsMigrationError, match="filename"):
        analytics_migrations.load_migrations()


@pytest.mark.parametrize(
    "migrations,reason",
    [
        ((), "contiguous"),
        ((Migration(0, "zero.sql", "SELECT 1"),), "contiguous"),
        ((revisions()[0], revisions()[0]), "contiguous"),
        ((revisions()[1], revisions()[0]), "contiguous"),
        ((revisions()[0], Migration(3, "gap.sql", "SELECT 3")), "contiguous"),
        ((Migration(1, "", "SELECT 1"),), "SQL statements"),
        ((Migration(1, "empty.sql", " ; "),), "SQL statements"),
        ((Migration(1, "empty.sql", "SELECT 1\n-- statement\n"),), "SQL statements"),
    ],
)
def test_invalid_packaged_history_is_rejected_before_contacting_clickhouse(migrations, reason):
    client = JournalClient()
    with pytest.raises(AnalyticsMigrationError, match=reason):
        ClickHouseMigrationRunner(client, migrations=migrations)
    assert client.statements == client.inserts == []


def test_current_is_read_only_and_check_rejects_missing_or_partial_schema():
    async def run():
        migrations = revisions()
        client = JournalClient()
        runner = ClickHouseMigrationRunner(client, migrations=migrations)
        assert await runner.current() == MigrationStatus(0, 2, (1, 2))
        with pytest.raises(AnalyticsMigrationError, match="analytics upgrade"):
            await runner.check()
        client.exists = True
        client.rows = [acknowledgement(migrations[0])]
        assert await runner.current() == MigrationStatus(1, 2, (2,))
        with pytest.raises(AnalyticsMigrationError, match="analytics upgrade"):
            await runner.check()
        assert client.statements == client.inserts == []

    asyncio.run(run())


@pytest.mark.parametrize("response", [[], [{"result": True}], [{"result": "1"}], [{}], [{}, {}]])
def test_malformed_table_existence_never_creates_or_acknowledges_schema(response):
    async def run():
        client = JournalClient()
        client.query = AsyncMock(return_value=response)
        with pytest.raises(AnalyticsMigrationError, match="history response"):
            await ClickHouseMigrationRunner(client, migrations=revisions()).upgrade()
        assert client.statements == client.inserts == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([{"version": 0}], "Unknown"),
        ([{"version": 3}], "Unknown"),
        ([{"version": True}], "Unknown"),
        ([{"version": "1"}], "Unknown"),
        ([{}], "Unknown"),
        ([{**acknowledgement(revisions()[0]), "name": "renamed.sql"}], "differs"),
        ([{**acknowledgement(revisions()[0]), "checksum": "changed"}], "differs"),
        ([acknowledgement(revisions()[1])], "version gap"),
        (
            [acknowledgement(revisions()[0]), {"version": 1, "name": "conflict"}],
            "differs",
        ),
    ],
)
def test_unknown_drifted_and_gapped_server_history_fails_before_any_ddl(rows, reason):
    async def run():
        client = JournalClient(exists=True, rows=rows)
        runner = ClickHouseMigrationRunner(client, migrations=revisions())
        with pytest.raises(AnalyticsMigrationError, match=reason):
            await runner.upgrade()
        assert client.statements == client.inserts == []

    asyncio.run(run())


def test_fresh_upgrade_acknowledges_each_revision_then_current_restart_executes_no_ddl():
    async def run():
        migrations = revisions()
        client = JournalClient()
        runner = ClickHouseMigrationRunner(client, migrations=migrations)
        assert await runner.upgrade() == (1, 2)
        assert client.statements == [
            sql for migration in migrations for sql in migration.statements
        ]
        assert client.rows == [acknowledgement(migration) for migration in migrations]
        client.rows.append(dict(client.rows[-1]))
        assert await runner.check() == MigrationStatus(2, 2, ())
        client.statements.clear()
        client.inserts.clear()
        assert await runner.upgrade() == ()
        assert client.statements == client.inserts == []

    asyncio.run(run())


def test_failed_second_statement_keeps_previous_revision_and_replays_unacknowledged_revision():
    async def run():
        migrations = revisions()
        client = JournalClient()
        client.fail_statement = migrations[1].statements[1]
        runner = ClickHouseMigrationRunner(client, migrations=migrations)
        with pytest.raises(AnalyticsUnavailable, match="DDL interrupted"):
            await runner.upgrade()
        assert client.rows == [acknowledgement(migrations[0])]
        assert await runner.current() == MigrationStatus(1, 2, (2,))
        client.statements.clear()
        assert await runner.upgrade() == (2,)
        assert client.statements == list(migrations[1].statements)
        assert await runner.check() == MigrationStatus(2, 2, ())

    asyncio.run(run())


@pytest.mark.parametrize("failure,pending", [("before", (1,)), ("after", ())])
def test_interrupted_journal_acknowledgement_reconciles_durable_history_before_replay(
    failure, pending
):
    async def run():
        migration = revisions()[0]
        client = JournalClient()
        client.fail_acknowledgement = failure
        runner = ClickHouseMigrationRunner(client, migrations=(migration,))
        with pytest.raises(AnalyticsUnavailable, match="Acknowledgement interrupted"):
            await runner.upgrade()
        assert client.exists
        assert (await runner.current()).pending_versions == pending
        client.statements.clear()
        assert await runner.upgrade() == pending
        assert client.statements == (list(migration.statements) if pending else [])
        assert await runner.check() == MigrationStatus(1, 1, ())

    asyncio.run(run())


def test_existing_journal_without_acknowledged_bootstrap_is_safely_recovered():
    async def run():
        client = JournalClient(exists=True)
        migrations = revisions()
        assert await ClickHouseMigrationRunner(client, migrations=migrations).upgrade() == (1, 2)
        assert client.rows == [acknowledgement(migration) for migration in migrations]

    asyncio.run(run())


@pytest.mark.parametrize("locked", [False, None])
def test_deployment_upgrade_rejects_a_busy_lock_without_opening_clickhouse(monkeypatch, locked):
    events = []

    @asynccontextmanager
    async def connect():
        events.append("postgres entered")
        try:
            result = SimpleNamespace(
                fetchone=AsyncMock(return_value=None if locked is None else {"locked": locked})
            )
            connection = SimpleNamespace(execute=AsyncMock(return_value=result))
            yield connection
            connection.execute.assert_awaited_once_with(
                "SELECT pg_try_advisory_lock(%s) AS locked", (SCHEMA_LOCK_KEY,)
            )
        finally:
            events.append("postgres closed")

    factory = SimpleNamespace(
        database_url="postgresql://unit@invalid/unit_test",
        database_connect_timeout_seconds=3,
    )
    connector = AsyncMock()
    monkeypatch.setattr(analytics_connections, "postgres_connections", lambda *a, **kw: connect)
    monkeypatch.setattr(analytics_migrations, "AnalyticsClient", connector)
    with pytest.raises(AnalyticsMigrationError, match="Another ClickHouse schema upgrade"):
        asyncio.run(analytics_migrations.upgrade_analytics(factory))
    connector.assert_not_called()
    assert events == ["postgres entered", "postgres closed"]


@pytest.mark.parametrize("failure", [None, "upgrade", "prepare", "check"])
def test_deployment_upgrade_closes_schema_lock_on_success_and_every_failed_phase(
    monkeypatch, failure
):
    events = []
    settings = SimpleNamespace(
        database_url="postgresql://unit@invalid/unit_test",
        database_connect_timeout_seconds=3,
        analytics_backfill_batch_size=200,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    @asynccontextmanager
    async def connect():
        events.append("postgres entered")
        try:
            result = SimpleNamespace(fetchone=AsyncMock(return_value={"locked": True}))
            yield SimpleNamespace(execute=AsyncMock(return_value=result))
        finally:
            events.append("postgres closed")

    def connections(url, *, connect_timeout):
        assert url == settings.database_url and connect_timeout == 3
        return connect

    @asynccontextmanager
    async def client_context(configured):
        assert configured is settings
        events.append("clickhouse entered")
        try:
            yield client
        finally:
            events.append("clickhouse closed")

    async def phase(name, result=None):
        events.append(name)
        if failure == name:
            raise AnalyticsUnavailable(name + " failed")
        return result

    status = MigrationStatus(2, 2, ())
    client = object()
    runner = SimpleNamespace(
        upgrade=lambda: phase("upgrade", (1, 2)), check=lambda: phase("check", status)
    )

    def importer(actual, actual_connect, *, settings, page_size):
        assert actual is client and actual_connect is connect and page_size == 200
        return SimpleNamespace(prepare=lambda: phase("prepare"))

    monkeypatch.setattr(analytics_connections, "postgres_connections", connections)
    monkeypatch.setattr(analytics_import_runner, "AnalyticsImportRunner", importer)
    monkeypatch.setattr(analytics_migrations, "AnalyticsClient", client_context)
    monkeypatch.setattr(analytics_migrations, "ClickHouseMigrationRunner", lambda actual: runner)
    if failure:
        with pytest.raises(AnalyticsUnavailable, match=failure + " failed"):
            asyncio.run(analytics_migrations.upgrade_analytics())
    else:
        assert asyncio.run(analytics_migrations.upgrade_analytics()) == status
    phases = ["upgrade", "prepare", "check"]
    if failure:
        phases = phases[: phases.index(failure) + 1]
    assert events == [
        "postgres entered",
        "clickhouse entered",
        *phases,
        "clickhouse closed",
        "postgres closed",
    ]


@pytest.mark.integration
@pytest.mark.parametrize("adopt_unversioned", [False, True])
def test_real_clickhouse_upgrade_preserves_data_and_replays_partial_revision(adopt_unversioned):
    configured = integration_settings()

    async def run():
        target = f"migrations_{uuid.uuid4().hex}_test"
        base = analytics_migrations.load_migrations()
        extension = Migration(
            len(base) + 1,
            f"{len(base) + 1:04d}_retained_totals.sql",
            "ALTER TABLE analytics_article_open_baseline "
            "ADD COLUMN IF NOT EXISTS upgrade_tag String DEFAULT 'ready;retry';\n"
            "-- statement\n"
            "ALTER TABLE analytics_article_open_baseline "
            "ADD COLUMN IF NOT EXISTS upgrade_number UInt8 DEFAULT 7;",
        )
        settings = configured.model_copy(update={"clickhouse_database": target})
        async with AnalyticsClient(
            configured.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(settings) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    runner = ClickHouseMigrationRunner(client, migrations=base)
                    assert (await runner.current()).current_version == 0
                    if adopt_unversioned:
                        for migration in base[1:]:
                            for statement in migration.statements:
                                await client.execute(statement)
                    else:
                        assert await runner.upgrade() == tuple(range(1, len(base) + 1))
                    article = uuid.uuid4()
                    now = datetime.now(UTC)
                    opens = ArticleOpenAnalytics(client)
                    await opens.import_totals([{"article_id": article, "opens": 11}])
                    await opens.record(article, "a" * 64, now)
                    await opens.record(article, "a" * 64, now)
                    if adopt_unversioned:
                        assert await runner.upgrade() == tuple(range(1, len(base) + 1))
                    assert await opens.totals([article], cutover=now) == {str(article): 12}
                    interrupted = True
                    commands = []

                    async def execute(statement):
                        nonlocal interrupted
                        commands.append(statement)
                        if statement == extension.statements[1] and interrupted:
                            interrupted = False
                            raise AnalyticsUnavailable("deployment interrupted")
                        await client.execute(statement)

                    transport = SimpleNamespace(
                        query=client.query, insert=client.insert, execute=execute
                    )
                    upgraded = ClickHouseMigrationRunner(transport, migrations=(*base, extension))
                    with pytest.raises(AnalyticsUnavailable, match="deployment interrupted"):
                        await upgraded.upgrade()
                    assert (await upgraded.current()).current_version == len(base)
                    columns = {
                        row["name"]
                        for row in await client.query(
                            "DESCRIBE TABLE analytics_article_open_baseline"
                        )
                    }
                    assert "upgrade_tag" in columns and "upgrade_number" not in columns
                    commands.clear()
                    assert await upgraded.upgrade() == (extension.version,)
                    assert commands == list(extension.statements)
                    assert await client.query(
                        "SELECT opens,upgrade_tag,upgrade_number "
                        "FROM analytics_article_open_baseline"
                    ) == [{"opens": 11, "upgrade_tag": "ready;retry", "upgrade_number": 7}]
                    assert await opens.totals([article], cutover=now) == {str(article): 12}
                async with AnalyticsClient(settings) as reconnected:
                    reconnected.execute = AsyncMock(wraps=reconnected.execute)
                    runner = ClickHouseMigrationRunner(reconnected, migrations=(*base, extension))
                    assert await runner.check() == MigrationStatus(
                        extension.version, extension.version, ()
                    )
                    assert await runner.upgrade() == ()
                    reconnected.execute.assert_not_awaited()
                    changed = Migration(extension.version, extension.name, extension.sql + "\n")
                    with pytest.raises(AnalyticsMigrationError, match="differs"):
                        await ClickHouseMigrationRunner(
                            reconnected, migrations=(*base, changed)
                        ).upgrade()
                    reconnected.execute.assert_not_awaited()
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())


@pytest.mark.integration
def test_real_postgres_schema_lock_serializes_separate_deployment_connections(monkeypatch):
    configured = integration_settings()

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        status = MigrationStatus(2, 2, ())
        clients = []

        @asynccontextmanager
        async def client_context(settings):
            assert settings is configured
            client = object()
            clients.append(client)
            yield client

        async def upgrade():
            entered.set()
            await release.wait()

        monkeypatch.setattr(analytics_migrations, "AnalyticsClient", client_context)
        monkeypatch.setattr(
            analytics_migrations,
            "ClickHouseMigrationRunner",
            lambda client: SimpleNamespace(upgrade=upgrade, check=AsyncMock(return_value=status)),
        )
        monkeypatch.setattr(
            analytics_import_runner,
            "AnalyticsImportRunner",
            lambda *args, **kwargs: SimpleNamespace(prepare=AsyncMock()),
        )
        first = asyncio.create_task(analytics_migrations.upgrade_analytics(configured))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            with pytest.raises(AnalyticsMigrationError, match="Another ClickHouse schema upgrade"):
                await analytics_migrations.upgrade_analytics(configured)
            assert len(clients) == 1
        finally:
            release.set()
            assert await first == status
        assert await analytics_migrations.upgrade_analytics(configured) == status
        assert len(clients) == 2

    asyncio.run(run())
