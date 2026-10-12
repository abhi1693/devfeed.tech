"""The cleanup index preserves delivery deduplication and retained event data."""

import importlib
from datetime import date
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import Base
from sqlalchemy import Column, MetaData, Table, Uuid, inspect, text


@pytest.mark.integration
@pytest.mark.parametrize("populated", [False, True], ids=["fresh", "existing-events"])
def test_delivery_event_retention_migration_preserves_rows_and_downgrades(database, populated):
    migration = importlib.import_module("migrations.versions.0036_partner_delivery_event_retention")
    metadata = MetaData()
    Table("partner_assets", metadata, Column("id", Uuid(), primary_key=True))
    Base.metadata.tables["partner_delivery_events"].to_metadata(metadata)
    asset_id, delivery_id = uuid4(), uuid4()
    events = [
        {"asset": asset_id, "delivery": delivery_id, "kind": kind, "day": day}
        for kind, day in (("impression", date(2026, 10, 1)), ("click", date(2026, 10, 13)))
    ]

    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_event_retention_migration_fixture"))
            connection.execute(
                text("SET LOCAL search_path TO partner_event_retention_migration_fixture")
            )
            connection.execute(text("CREATE TABLE partner_assets (id uuid PRIMARY KEY)"))
            connection.execute(
                text("""CREATE TABLE partner_delivery_events (
                    asset_id uuid NOT NULL REFERENCES partner_assets(id),
                    delivery_id uuid NOT NULL,
                    kind varchar(20) NOT NULL,
                    day date NOT NULL,
                    PRIMARY KEY (asset_id, delivery_id, kind),
                    CONSTRAINT ck_partner_delivery_kind CHECK (kind IN ('impression','click'))
                )""")
            )
            if populated:
                connection.execute(
                    text("INSERT INTO partner_assets VALUES (:asset)"), {"asset": asset_id}
                )
                connection.execute(
                    text("""INSERT INTO partner_delivery_events
                        (asset_id, delivery_id, kind, day)
                        VALUES (:asset, :delivery, :kind, :day)"""),
                    events,
                )
            before = connection.execute(
                text("SELECT * FROM partner_delivery_events ORDER BY day")
            ).all()
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_name": lambda name, kind, parent: (
                        kind != "table" or name in metadata.tables
                    ),
                },
            )
            with Operations.context(context):
                migration.upgrade()
                assert compare_metadata(context, metadata) == []
                indexes = inspect(connection).get_indexes("partner_delivery_events")
                cleanup_index = next(
                    index for index in indexes if index["name"] == "ix_partner_delivery_event_day"
                )
                assert cleanup_index["column_names"] == ["day"]
                assert cleanup_index["unique"] is False
                assert inspect(connection).get_pk_constraint("partner_delivery_events")[
                    "constrained_columns"
                ] == ["asset_id", "delivery_id", "kind"]
                assert (
                    connection.execute(
                        text("SELECT * FROM partner_delivery_events ORDER BY day")
                    ).all()
                    == before
                )

                migration.downgrade()
                assert inspect(connection).get_indexes("partner_delivery_events") == []
                assert (
                    connection.execute(
                        text("SELECT * FROM partner_delivery_events ORDER BY day")
                    ).all()
                    == before
                )
                assert "partner_assets" in inspect(connection).get_table_names()
        finally:
            transaction.rollback()
