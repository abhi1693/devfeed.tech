"""Partnership migrations match the models and preserve existing data."""

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


def test_partnership_revisions_follow_master_schema():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [SCHEMA_REVISION] == ["0026"]
    assert scripts.get_revision("0026").down_revision == "0025"
    assert scripts.get_revision("0025").down_revision == "0024"
    assert scripts.get_revision("0024").down_revision == "0023"
    assert scripts.get_revision("0023").down_revision == "0022"
    assert [revision.revision for revision in scripts.walk_revisions("0022", "head")] == [
        "0026",
        "0025",
        "0024",
        "0023",
        "0022",
    ]


@pytest.mark.integration
def test_consolidated_migration_matches_models_and_downgrades_cleanly(database):
    path = ROOT / "migrations/versions/0021_partnerships.py"
    spec = importlib.util.spec_from_file_location("partner_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    tier_spec = importlib.util.spec_from_file_location(
        "tier_migration", ROOT / "migrations/versions/0024_partner_account_tiers.py"
    )
    tier_migration = importlib.util.module_from_spec(tier_spec)
    tier_spec.loader.exec_module(tier_migration)
    account_spec = importlib.util.spec_from_file_location(
        "connection_account_migration",
        ROOT / "migrations/versions/0025_partner_connection_account.py",
    )
    account_migration = importlib.util.module_from_spec(account_spec)
    account_spec.loader.exec_module(account_migration)
    connector_spec = importlib.util.spec_from_file_location(
        "connector_migration", ROOT / "migrations/versions/0026_partner_connector_definitions.py"
    )
    connector_migration = importlib.util.module_from_spec(connector_spec)
    connector_spec.loader.exec_module(connector_migration)
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
                tier_migration.upgrade()
                account_migration.upgrade()
                connection.execute(
                    text("""INSERT INTO partner_connections
                    (provider, enabled, revision, next_sync_at, updated_by)
                    VALUES ('nick-launches', true, 1, now(), '{}'::jsonb)""")
                )
                connection.execute(
                    text("""INSERT INTO partner_pipeline_jobs
                    (id, provider, operation, payload, status, attempts, available_at, created_at)
                    VALUES ('11111111-1111-4111-8111-111111111111', 'nick-launches', 'sync',
                    CAST(:payload AS jsonb), 'queued', 0, now(), now())"""),
                    {"payload": '{"cursor":"saved-cursor","pages":3}'},
                )
                connector_migration.upgrade()
                assert connection.execute(
                    text(
                        "SELECT payload->>'cursor', payload->>'pages', "
                        "payload->'connector'->>'base_url' FROM partner_pipeline_jobs"
                    )
                ).one() == ("saved-cursor", "3", "https://nicklaunches.com")
                assert connection.execute(
                    text("SELECT name, connector->>'base_url' FROM partner_connections")
                ).one() == ("Nick Launches", "https://nicklaunches.com")
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
                assert connection.execute(
                    text("SELECT sync_interval_minutes, sync_revision FROM partner_connections")
                ).one() == (360, 1)
                for interval in (0, 10081):
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            text("UPDATE partner_connections SET sync_interval_minutes=:interval"),
                            {"interval": interval},
                        )
                connector_migration.downgrade()
                account_migration.downgrade()
                tier_migration.downgrade()
                migration.downgrade()
                assert not partner_names.intersection(inspect(connection).get_table_names())
                assert connection.scalar(text("SELECT value FROM existing_data")) == "keep me"
                migration.upgrade()
                tier_migration.upgrade()
                account_migration.upgrade()
                connector_migration.upgrade()
                assert compare_metadata(context, metadata) == []
        finally:
            transaction.rollback()
