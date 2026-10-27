"""Migration helpers load when Alembic runs outside the repository."""

import asyncio
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from devfeed_cli import commands
from devfeed_core.db import get_engine
from devfeed_core.models import Article, IngestionJob, Source
from devfeed_core.version import SCHEMA_REVISION
from devfeed_http.health import readiness_response
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.support.legacy_migrations import database_at_revision
from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT


def test_migration_history_loads_from_an_unrelated_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "history"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "0027 -> 0028" in result.stdout
    assert "0028 -> 0029" in result.stdout
    assert f"-> {SCHEMA_REVISION} (head)" in result.stdout


@pytest.mark.integration
def test_timer_index_upgrade_preserves_existing_card_readiness_and_business_data(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0042") as database:
        _write_history(database)
        expected = _history(database)
        with database.begin() as session:
            session.execute(
                text(
                    "UPDATE analytics_pipeline_state SET card_target_policy='existing',"
                    "card_serving_policy='existing',card_bootstrap_complete=true"
                )
            )
        _upgrade()
        assert _history(database) == expected
        with database() as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
            )
            state = session.execute(
                text(
                    "SELECT card_target_policy,card_serving_policy,card_bootstrap_complete "
                    "FROM analytics_pipeline_state WHERE singleton"
                )
            ).one()
            assert tuple(state) == ("existing", "existing", True)
            bootstrap = session.execute(
                text("SELECT policy,epoch,complete FROM analytics_card_timer_bootstrap")
            ).one()
            assert tuple(bootstrap) == (None, 0, False)
        inspector = inspect(get_engine())
        timers = {
            column["name"]: column for column in inspector.get_columns("analytics_card_timers")
        }
        assert set(timers) == {"policy", "kind", "entity_id", "epoch", "generation", "available_at"}
        assert timers["available_at"]["nullable"]
        assert all(not timers[name]["nullable"] for name in set(timers) - {"available_at"})
        assert inspector.get_pk_constraint("analytics_card_timers")["constrained_columns"] == [
            "policy",
            "kind",
            "entity_id",
        ]
        indexes = {index["name"]: index for index in inspector.get_indexes("analytics_card_timers")}
        due = indexes["ix_analytics_card_timers_due"]
        assert due["column_names"] == ["policy", "epoch", "available_at", "kind", "entity_id"]
        assert "available_at IS NOT NULL" in due["dialect_options"]["postgresql_where"]
        assert indexes["ix_analytics_card_timers_epoch"]["column_names"] == [
            "policy",
            "epoch",
            "kind",
            "entity_id",
        ]
        tombstone = indexes["ix_analytics_card_timers_tombstone"]
        assert tombstone["column_names"] == ["policy", "epoch", "kind", "entity_id"]
        assert "available_at IS NULL" in tombstone["dialect_options"]["postgresql_where"]
        assert {
            column["name"] for column in inspector.get_columns("analytics_card_timer_bootstrap")
        } == {
            "singleton",
            "policy",
            "epoch",
            "kind_index",
            "shard",
            "cursor",
            "complete",
            "cleanup_pending",
        }


@pytest.mark.integration
def test_timer_upgrade_fences_old_prepared_exporter_and_replays_pending_events(
    integration_environment, monkeypatch
):
    import psycopg
    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.analytics_worker import AnalyticsExporter
    from psycopg import sql

    from tests.support.analytics import settings as analytics_settings

    with database_at_revision(monkeypatch, "0042") as database:
        with database.begin() as session:
            session.execute(
                text(
                    "UPDATE analytics_pipeline_state SET enabled=true,"
                    "capture_started_at=clock_timestamp() WHERE singleton"
                )
            )
        _write_history(database)
        with database() as session:
            pending = set(session.scalars(text("SELECT id FROM analytics_ingest_events")))
        assert pending
        source = database.kw["bind"].url.render_as_string(hide_password=False)
        dsn = source.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(dsn, autocommit=True) as old:
            old.execute(
                "PREPARE old_claim(uuid) AS UPDATE analytics_pipeline_state "
                "SET lease_token=$1,lease_until=clock_timestamp()+interval '1 hour' "
                "WHERE singleton RETURNING lease_generation"
            )
            claim = sql.SQL("EXECUTE old_claim({})").format(sql.Literal(uuid.uuid4()))
            assert old.execute(claim).fetchone() is not None
            _upgrade()
            with pytest.raises(psycopg.errors.UndefinedColumn):
                old.execute(claim)
            with database() as session:
                assert session.execute(
                    text("SELECT native_lease_token,lease_until FROM analytics_pipeline_state")
                ).one() == (None, None)
                assert (
                    set(session.scalars(text("SELECT id FROM analytics_ingest_events"))) == pending
                )

            async def replay():
                configured = analytics_settings().model_copy(update={"database_url": source})
                name = f"devfeed_timer_fence_{uuid.uuid4().hex}_test"
                async with AnalyticsClient(configured) as admin:
                    await admin.execute(f"CREATE DATABASE `{name}`")
                    try:
                        current = configured.model_copy(update={"clickhouse_database": name})
                        async with AnalyticsClient(current) as client:
                            client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                            await client.initialize()
                            exporter = AnalyticsExporter(client, current)
                            try:
                                for _ in range(20):
                                    await exporter.once()
                                    with database() as session:
                                        remaining = set(
                                            session.scalars(
                                                text("SELECT id FROM analytics_ingest_events")
                                            )
                                        )
                                    if not pending.intersection(remaining):
                                        break
                                else:
                                    pytest.fail(
                                        "The new exporter did not acknowledge old captured events"
                                    )
                            finally:
                                exporter.close()
                    finally:
                        await admin.execute(f"DROP DATABASE `{name}` SYNC")

            asyncio.run(replay())


def _configuration():
    return Config(str(ROOT / "alembic.ini"))


def _write_history(database):
    with database.begin() as session:
        session.execute(text("SET LOCAL statement_timeout='2s'"))
        source = Source(
            name="Online upgrade source",
            feed_url=f"https://online-upgrade.invalid/{uuid.uuid4()}.rss",
            source_type="publisher",
        )
        session.add(source)
        session.flush()
        session.add(IngestionJob(source_id=source.id))
        session.add(
            Article(
                canonical_url=f"https://online-upgrade.invalid/{uuid.uuid4()}",
                url_hash=uuid.uuid4().hex,
                title="Retained during online upgrade",
            )
        )


def _history(database):
    with database() as session:
        return {
            table: session.execute(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id"))
            .scalars()
            .all()
            for table in ("articles", "sources", "ingestion_jobs")
        }


def _assert_old_app_available(database, revision="0037"):
    with database() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == revision
        assert session.scalar(text("SELECT to_regclass('analytics_pipeline_state')")) is None
    assert readiness_response(database(), Mock(), schema_revision=revision) == {"status": "ok"}
    _write_history(database)


def _upgrade(route="alembic"):
    if route == "cli":
        commands.migrate(SimpleNamespace(config=ROOT / "alembic.ini", action="upgrade"))
    else:
        command.upgrade(_configuration(), "head")


def _wait_for_index_writer_phase(engine, future):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            phase = connection.scalar(
                text("""SELECT phase FROM pg_stat_progress_create_index
                    WHERE relid='ingestion_jobs'::regclass AND datid=(
                      SELECT oid FROM pg_database WHERE datname=current_database())""")
            )
        if phase == "waiting for writers before build":
            return
        if future.done():
            future.result()
            pytest.fail("Concurrent index preparation finished without waiting for the writer")
        time.sleep(0.01)
    pytest.fail("Concurrent index preparation did not reach its writer barrier")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("revision", "route"), [("0037", "cli"), ("0037", "alembic"), ("0033", "alembic")]
)
def test_online_index_preparation_keeps_old_readiness_and_writes_available(
    integration_environment, monkeypatch, revision, route
):
    with database_at_revision(monkeypatch, revision) as database:
        _write_history(database)
        engine = get_engine()
        with engine.connect() as writer, ThreadPoolExecutor(max_workers=1) as executor:
            transaction = writer.begin()
            writer.execute(text("LOCK TABLE ingestion_jobs IN ROW EXCLUSIVE MODE"))
            future = executor.submit(_upgrade, route)
            try:
                # This is an actual in-progress PostgreSQL concurrent build,
                # delayed by an old writer rather than a mocked migration call.
                _wait_for_index_writer_phase(engine, future)
                _assert_old_app_available(database, revision)
                expected = _history(database)
            finally:
                transaction.rollback()
            future.result(timeout=30)
        with database() as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
            )
            assert session.scalar(text("SELECT count(*) FROM analytics_bootstrap_cursors")) > 0
        assert _history(database) == expected


@pytest.mark.integration
def test_failed_index_preparation_does_not_advance_schema_or_stop_old_writes(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0037") as database:
        engine = get_engine()

        def fail_build(_connection, _cursor, statement, *_args):
            if statement.startswith("CREATE INDEX CONCURRENTLY ix_ingestion_jobs_analytics"):
                raise RuntimeError("owned preparation failure")

        event.listen(engine, "before_cursor_execute", fail_build)
        try:
            with pytest.raises(RuntimeError, match="owned preparation failure"):
                _upgrade()
        finally:
            event.remove(engine, "before_cursor_execute", fail_build)
        _assert_old_app_available(database)
        expected = _history(database)
        _upgrade()
        assert _history(database) == expected


@pytest.mark.integration
def test_interrupted_index_preparation_restores_budgets_and_retries_invalid_indexes(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0037") as database:
        _write_history(database)
        expected = _history(database)
        engine = get_engine()
        migration = ScriptDirectory.from_config(_configuration()).get_revision("0040").module
        with (
            engine.connect() as writer,
            engine.connect().execution_options(isolation_level="AUTOCOMMIT") as preparation,
        ):
            transaction = writer.begin()
            writer.execute(text("LOCK TABLE ingestion_jobs IN ROW EXCLUSIVE MODE"))
            preparation.execute(text("SET statement_timeout='250ms'"))
            preparation.execute(text("SET lock_timeout='700ms'"))
            preparation.execute(text("SET max_parallel_maintenance_workers=3"))
            try:
                with pytest.raises(DBAPIError, match="statement timeout"):
                    migration.prepare_active_indexes(preparation)
                assert migration.active_index_state(preparation, "ingestion_jobs") is False
                assert preparation.scalar(text("SHOW statement_timeout")) == "250ms"
                assert preparation.scalar(text("SHOW lock_timeout")) == "700ms"
                assert preparation.scalar(text("SHOW max_parallel_maintenance_workers")) == "3"
                _assert_old_app_available(database)
            finally:
                transaction.rollback()
            # A failed concurrent build left a real invalid catalog entry.
            # Retry removes that identical incomplete entry and finishes all8.
            migration.prepare_active_indexes(preparation)
            assert all(
                migration.active_index_state(preparation, table) is True for table in migration.JOBS
            )
            assert preparation.scalar(text("SHOW statement_timeout")) == "250ms"
            assert preparation.scalar(text("SHOW lock_timeout")) == "700ms"
            assert preparation.scalar(text("SHOW max_parallel_maintenance_workers")) == "3"
        expected = _history(database)
        _upgrade()
        assert _history(database) == expected


@pytest.mark.integration
def test_post_preparation_cutover_failure_rolls_back_to_old_schema(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0037") as database:
        engine = get_engine()

        def fail_cutover(_connection, _cursor, statement, *_args):
            if statement.startswith("CREATE TABLE analytics_card_refresh"):
                raise RuntimeError("owned late cutover failure")

        event.listen(engine, "before_cursor_execute", fail_cutover)
        try:
            with pytest.raises(RuntimeError, match="owned late cutover failure"):
                _upgrade()
        finally:
            event.remove(engine, "before_cursor_execute", fail_cutover)
        _assert_old_app_available(database)
        expected = _history(database)
        _upgrade()
        assert _history(database) == expected


@pytest.mark.integration
def test_preparation_preserves_valid_mismatched_index_and_old_revision(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0037") as database:
        with database.begin() as session:
            session.execute(
                text("CREATE INDEX ix_ingestion_jobs_analytics_active ON ingestion_jobs(id)")
            )
        with pytest.raises(RuntimeError, match="does not match migration0040"):
            _upgrade()
        _assert_old_app_available(database)
        with database() as session:
            assert (
                session.scalar(
                    text(
                        "SELECT pg_get_indexdef("
                        "'ix_ingestion_jobs_analytics_active'::regclass,1,false)"
                    )
                )
                == "id"
            )


@pytest.mark.integration
def test_other_alembic_commands_do_not_prepare_future_indexes(integration_environment, monkeypatch):
    with database_at_revision(monkeypatch, "0037") as database:
        command.current(_configuration())
        with pytest.raises(CommandError, match="Target database is not up to date"):
            command.check(_configuration())
        command.stamp(_configuration(), "0037")
        command.upgrade(_configuration(), "0037")
        with database() as session:
            assert (
                session.scalar(text("SELECT to_regclass('ix_ingestion_jobs_analytics_active')"))
                is None
            )
        command.downgrade(_configuration(), "0036")
        with database() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0036"
            assert (
                session.scalar(text("SELECT to_regclass('ix_ingestion_jobs_analytics_active')"))
                is None
            )


def test_offline_cutover_requires_online_index_preparation():
    with pytest.raises(RuntimeError, match="requires an online upgrade"):
        command.upgrade(_configuration(), "0039:0040", sql=True)
    with pytest.raises(RuntimeError, match="requires an online upgrade"):
        command.upgrade(_configuration(), "0042:0043", sql=True)


@pytest.mark.integration
def test_timer_refresh_preparation_retries_interrupted_build(integration_environment, monkeypatch):
    with database_at_revision(monkeypatch, "0042") as database:
        engine = get_engine()
        migration = ScriptDirectory.from_config(_configuration()).get_revision("0043").module
        with (
            engine.connect() as writer,
            engine.connect().execution_options(isolation_level="AUTOCOMMIT") as preparation,
        ):
            transaction = writer.begin()
            writer.execute(text("LOCK TABLE analytics_card_refresh IN ROW EXCLUSIVE MODE"))
            preparation.execute(text("SET statement_timeout='250ms'"))
            preparation.execute(text("SET max_parallel_maintenance_workers=3"))
            try:
                with pytest.raises(DBAPIError, match="statement timeout"):
                    migration.prepare_refresh_index(preparation)
                assert migration.refresh_index_state(preparation) is False
                assert preparation.scalar(text("SHOW max_parallel_maintenance_workers")) == "3"
                assert preparation.scalar(text("SHOW statement_timeout")) == "250ms"
                with database() as session:
                    assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0042"
            finally:
                transaction.rollback()
            migration.prepare_refresh_index(preparation)
            assert migration.refresh_index_state(preparation) is True
        _upgrade()


@pytest.mark.integration
def test_timer_refresh_online_preparation_allows_existing_writes(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0042") as database:
        engine = get_engine()
        with engine.connect() as writer, ThreadPoolExecutor(max_workers=1) as executor:
            transaction = writer.begin()
            writer.execute(text("LOCK TABLE analytics_card_refresh IN ROW EXCLUSIVE MODE"))
            future = executor.submit(_upgrade)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with engine.connect() as connection:
                        phase = connection.scalar(
                            text("""SELECT phase FROM pg_stat_progress_create_index
                            WHERE relid='analytics_card_refresh'::regclass AND datid=(
                              SELECT oid FROM pg_database WHERE datname=current_database())""")
                        )
                    if phase == "waiting for writers before build":
                        break
                    if future.done():
                        future.result()
                        pytest.fail("Index preparation finished without waiting for the writer")
                    time.sleep(0.01)
                else:
                    pytest.fail("Index preparation did not reach its writer barrier")
                identifier = uuid.uuid4()
                with database.begin() as session:
                    assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0042"
                    session.execute(
                        text("""INSERT INTO analytics_card_refresh
                        (kind,entity_id,generation,initial_pending,available_at)
                        VALUES ('article',:id,1,true,statement_timestamp())"""),
                        {"id": identifier},
                    )
            finally:
                transaction.rollback()
            future.result(timeout=30)
        with database() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0043"
            assert (
                session.scalar(
                    text("SELECT count(*) FROM analytics_card_refresh WHERE entity_id=:id"),
                    {"id": identifier},
                )
                == 1
            )


@pytest.mark.integration
def test_timer_refresh_preparation_fails_closed_on_index_conflict_or_drop(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0042") as database:
        engine = get_engine()
        migration = ScriptDirectory.from_config(_configuration()).get_revision("0043").module
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX ix_analytics_card_refresh_initial_due "
                    "ON analytics_card_refresh(entity_id)"
                )
            )
        with pytest.raises(RuntimeError, match="does not match 0043"):
            _upgrade()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT pg_get_indexdef("
                        "'ix_analytics_card_refresh_initial_due'::regclass,1,false)"
                    )
                )
                == "entity_id"
            )
            connection.execute(text("DROP INDEX ix_analytics_card_refresh_initial_due"))
            migration.prepare_refresh_index(connection)
            connection.execute(text("DROP INDEX ix_analytics_card_refresh_initial_due"))
        with (
            engine.begin() as connection,
            pytest.raises(RuntimeError, match="Prepare the analytics card refresh index"),
        ):
            migration.require_refresh_index(connection)
        with database() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0042"
        _upgrade()
