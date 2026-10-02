"""The PR installs the final partnership schema in one reversible migration."""

import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from devfeed_core.models import Base
from devfeed_core.version import SCHEMA_REVISION
from sqlalchemy import CheckConstraint, MetaData, inspect, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).parents[1]


def test_partnership_is_one_revision_after_master_schema():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [SCHEMA_REVISION] == ["0021"]
    assert scripts.get_revision("0021").down_revision == "0020"
    assert [revision.revision for revision in scripts.walk_revisions("0020", "head")] == [
        "0021",
        "0020",
    ]


@pytest.mark.integration
def test_consolidated_migration_matches_models_and_downgrades_cleanly(database):
    path = ROOT / "migrations/versions/0021_partnerships.py"
    spec = importlib.util.spec_from_file_location("partner_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    partner_names = {name for name in Base.metadata.tables if name.startswith("partner_")}
    metadata = MetaData()
    for name in partner_names:
        Base.metadata.tables[name].to_metadata(metadata)

    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO partner_migration_fixture"))
            connection.execute(text("CREATE TABLE existing_data (value text NOT NULL)"))
            connection.execute(text("INSERT INTO existing_data VALUES ('keep me')"))
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_server_default": True,
                    "include_name": lambda name, kind, parent: (
                        kind != "table" or name in partner_names
                    ),
                },
            )
            with Operations.context(context):
                migration.upgrade()
                assert compare_metadata(context, metadata) == []
                inspector = inspect(connection)
                for name in partner_names:
                    expected_checks = {
                        constraint.name
                        for constraint in metadata.tables[name].constraints
                        if isinstance(constraint, CheckConstraint)
                    }
                    assert {
                        check["name"] for check in inspector.get_check_constraints(name)
                    } == expected_checks
                connection.execute(
                    text("""INSERT INTO partner_connections
                    (provider, enabled, revision, next_sync_at, updated_by)
                    VALUES ('nick-launches', true, 1, now(), '{}'::jsonb)""")
                )
                assert connection.execute(
                    text("SELECT sync_interval_minutes, sync_revision FROM partner_connections")
                ).one() == (360, 1)
                for interval in (0, 10081):
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            text("UPDATE partner_connections SET sync_interval_minutes=:interval"),
                            {"interval": interval},
                        )
                migration.downgrade()
                assert not partner_names.intersection(inspect(connection).get_table_names())
                assert connection.scalar(text("SELECT value FROM existing_data")) == "keep me"
                migration.upgrade()
                assert compare_metadata(context, metadata) == []
        finally:
            transaction.rollback()
