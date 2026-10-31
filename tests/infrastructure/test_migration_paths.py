"""The direct migration preserves history for a bounded, asynchronous import."""

import asyncio
import subprocess
import sys
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from devfeed_core.analytics_cutover import AnalyticsCutoverBusy, LegacyAnalyticsCutover
from devfeed_core.config import get_settings
from devfeed_core.models import Article
from devfeed_core.version import SCHEMA_REVISION
from psycopg.rows import dict_row
from sqlalchemy import text

from tests.support.legacy_migrations import database_at_revision
from tests.support.paths import REPO_ROOT


def test_migration_history_loads_from_unrelated_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "history"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"0039 -> {SCHEMA_REVISION} (head)" in result.stdout
    scripts = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    assert scripts.get_revision(SCHEMA_REVISION).down_revision == "0039"


@pytest.mark.integration
def test_direct_upgrade_preserves_old_analytics_and_creates_no_capture_tables(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0037") as database:
        with database.begin() as session:
            session.execute(
                text(
                    "INSERT INTO overview_daily(day,metrics,updated_at) VALUES "
                    "('2026-01-01',jsonb_build_object('opens',12),now())"
                )
            )
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
        with database() as session:
            assert session.scalar(text("SELECT metrics FROM overview_daily")) == {"opens": 12}
            assert session.scalar(text("SELECT to_regclass('analytics_pipeline_state')")) is None
            assert session.scalar(text("SELECT to_regclass('analytics_ingest_events')")) is None
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
            )
            columns = set(
                session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='partner_placement_deliveries'"
                    )
                )
            )
            assert {"impression_recorded_at", "click_recorded_at"} <= columns


@pytest.mark.integration
def test_short_cutover_rejects_old_metrics_and_keeps_business_writes_available(
    integration_environment, monkeypatch
):
    from devfeed_core import analytics_producer

    monkeypatch.setattr(analytics_producer, "submit", lambda changes: None)
    with database_at_revision(monkeypatch, "0037") as database:
        article_id = uuid.uuid4()
        with database.begin() as session:
            session.add(
                Article(
                    id=article_id,
                    title="Keep source writable",
                    canonical_url=f"https://cutover.invalid/{article_id}",
                    url_hash=article_id.hex,
                )
            )
        with database.begin() as session:
            session.execute(
                text("INSERT INTO article_engagement(article_id,opens) VALUES (:id,12)"),
                {"id": article_id},
            )
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)

        async def run():
            async with (
                await psycopg.AsyncConnection.connect(
                    dsn, autocommit=True, row_factory=dict_row
                ) as connection,
                await psycopg.AsyncConnection.connect(dsn, autocommit=True) as writer,
            ):
                cutover = LegacyAnalyticsCutover()
                async with writer.transaction():
                    await writer.execute(
                        "UPDATE articles SET title='in flight' WHERE id=%s", (article_id,)
                    )
                    with pytest.raises(AnalyticsCutoverBusy):
                        await cutover.freeze(connection)
                assert not await cutover.frozen(connection)
                await cutover.freeze(connection)
                assert await cutover.frozen(connection)
                with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                    await connection.execute("UPDATE article_engagement SET opens=13")
                await connection.execute("DELETE FROM articles WHERE id=%s", (article_id,))
                row = await (
                    await connection.execute(
                        "SELECT opens FROM article_engagement WHERE article_id=%s", (article_id,)
                    )
                ).fetchone()
                assert row["opens"] == 12
                await cutover.cleanup(connection)
                assert not await cutover.tables(connection)
                await cutover.cleanup(connection)
                assert await cutover.frozen(connection)

        asyncio.run(run())
