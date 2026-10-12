"""Enrollment cursors survive catalog changes and clean up with their account."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import Base
from sqlalchemy import Column, MetaData, Table, Uuid, inspect, text


@pytest.mark.integration
def test_enrollment_migration_preserves_cursors_and_reverses_cleanly(database):
    path = Path(__file__).parents[1] / "migrations/versions/0035_partner_promotion_enrollment.py"
    spec = importlib.util.spec_from_file_location("partner_enrollment_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    account_id, other_account_id, product_id = uuid4(), uuid4(), uuid4()
    metadata = MetaData()
    Table("partner_accounts", metadata, Column("id", Uuid(), primary_key=True))
    Table("partner_products", metadata, Column("id", Uuid(), primary_key=True))
    Base.metadata.tables["partner_promotion_enrollments"].to_metadata(metadata)

    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_enrollment_migration_fixture"))
            connection.execute(
                text("SET LOCAL search_path TO partner_enrollment_migration_fixture")
            )
            connection.execute(text("CREATE TABLE partner_accounts (id uuid PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE partner_products (id uuid PRIMARY KEY)"))
            connection.execute(
                text("INSERT INTO partner_accounts VALUES (:id)"), {"id": account_id}
            )
            connection.execute(
                text("INSERT INTO partner_accounts VALUES (:id)"), {"id": other_account_id}
            )
            connection.execute(
                text("INSERT INTO partner_products VALUES (:id)"), {"id": product_id}
            )
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_server_default": True,
                    "include_name": lambda name, kind, parent: (
                        kind != "table" or name in metadata.tables
                    ),
                },
            )
            with Operations.context(context):
                migration.upgrade()
                assert compare_metadata(context, metadata) == []
                connection.execute(
                    text("""INSERT INTO partner_promotion_enrollments
                        (account_id, product_cursor) VALUES (:account, :product)"""),
                    {"account": account_id, "product": product_id},
                )
                connection.execute(
                    text("DELETE FROM partner_products WHERE id = :id"), {"id": product_id}
                )
                enrollment = connection.execute(
                    text("SELECT * FROM partner_promotion_enrollments")
                ).one()
                assert enrollment.product_cursor == product_id
                assert enrollment.requested_at is not None
                assert enrollment.updated_at is not None
                assert enrollment.available_at is not None
                assert enrollment.sweep_started_at is None
                connection.execute(
                    text("DELETE FROM partner_accounts WHERE id = :id"), {"id": account_id}
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM partner_promotion_enrollments")
                    ).scalar_one()
                    == 0
                )
                migration.downgrade()
                assert "partner_promotion_enrollments" not in inspect(connection).get_table_names()
                assert connection.execute(
                    text("SELECT id FROM partner_accounts")
                ).scalars().all() == [other_account_id]
        finally:
            transaction.rollback()
