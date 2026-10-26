"""The migrations-free admin image fixture starts the native capture schema."""

import asyncio
import importlib.util
import uuid

import psycopg
import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_schema import BACKFILL_TABLES
from devfeed_core.analytics_worker import AnalyticsExporter
from devfeed_core.config import Settings
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration


def test_fleet_metadata_fixture_keeps_source_records_and_can_activate_native_capture(
    database, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "fleet_runtime_metadata", REPO_ROOT / "scripts/ci/fleet_contracts/runtime.py"
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    owner = database.kw["bind"]
    schema = "fleet_metadata_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        owner.url,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    fixture = engine.execution_options(schema_translate_map={None: schema})
    monkeypatch.setattr(runtime, "get_engine", lambda: fixture)
    try:
        runtime.metadata_setup()
        runtime.retained()
        with fixture.connect() as connection:
            state = connection.execute(text("SELECT * FROM analytics_pipeline_state")).one()
            assert state.enabled is False and state.native_import_complete is False
            assert state.native_bootstrap_barrier is False
            assert state.native_bootstrap_complete is False
            assert state.native_capture_started_at is None and state.reader_coverage_start is None
            assert (
                connection.scalar(
                    text(
                        "INSERT INTO analytics_capture_events(source_table,operation,record) "
                        "VALUES('articles','I','{}'::jsonb) RETURNING format_version"
                    )
                )
                == 2
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO analytics_capture_events("
                        "source_table,operation,record,format_version) "
                        "VALUES('articles','I','{}'::jsonb,3)"
                    )
                )
            indexes = set(
                connection.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname=:schema "
                        "AND tablename='analytics_fact_refresh'"
                    ),
                    {"schema": schema},
                )
            )
            assert {
                "ix_analytics_fact_refresh_requested",
                "ix_analytics_fact_refresh_due",
                "ix_analytics_fact_refresh_domain_due",
            } <= indexes
            assert connection.execute(
                text(
                    "SELECT domain,shard,bootstrap_required,bootstrap_generation "
                    "FROM analytics_gauge_refreshes ORDER BY domain"
                )
            ).all() == [(name, 0, False, 0) for name in ("feed", "queues", "sources", "topics")]
            assert connection.scalar(text("SELECT started FROM analytics_gauge_bootstrap")) is False
            for table in (
                "analytics_fact_refresh",
                "analytics_rollup_compaction",
                "analytics_upgrade_checkpoint",
            ):
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0

        async def activate():
            settings = Settings(
                _env_file=None,
                database_url=owner.url.render_as_string(hide_password=False),
                redis_url="redis://localhost/15",
            )
            exporter = AnalyticsExporter(AnalyticsClient(settings), settings)
            async with await psycopg.AsyncConnection.connect(
                settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1),
                autocommit=True,
                row_factory=dict_row,
                options=f"-csearch_path={schema},public",
            ) as connection:
                await exporter._enable_capture(connection)
                row = await (
                    await connection.execute(
                        "SELECT enabled,capture_started_at FROM analytics_pipeline_state"
                    )
                ).fetchone()
                assert row["enabled"] is True and row["capture_started_at"] is not None
                rows = await (
                    await connection.execute(
                        "SELECT source_table FROM analytics_bootstrap_cursors WHERE NOT complete"
                    )
                ).fetchall()
                assert {row["source_table"] for row in rows} == set(BACKFILL_TABLES)

        asyncio.run(activate())
        runtime.retained()
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
