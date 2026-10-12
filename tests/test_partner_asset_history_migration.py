"""Product history indexing preserves every asset identity and issued delivery."""

import importlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import Base
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, Uuid, inspect, select, text


@pytest.mark.integration
@pytest.mark.parametrize("populated", [False, True], ids=["fresh", "historical-assets"])
def test_asset_history_migration_preserves_data_and_downgrades(database, populated):
    migration = importlib.import_module("migrations.versions.0037_partner_asset_history_index")
    metadata = MetaData()
    for name in ("partner_accounts", "partner_products", "articles"):
        Table(name, metadata, Column("id", Uuid(), primary_key=True))
    Table(
        "partner_campaigns",
        metadata,
        Column("id", Uuid(), primary_key=True),
        Column("account_id", Uuid(), nullable=False),
        UniqueConstraint("account_id", "id"),
    )
    assets = Base.metadata.tables["partner_assets"].to_metadata(metadata)
    deliveries = Base.metadata.tables["partner_placement_deliveries"].to_metadata(metadata)
    history_index = next(
        index for index in assets.indexes if index.name == "ix_partner_asset_product"
    )
    assets.indexes.remove(history_index)
    account_id, product_id = uuid4(), uuid4()
    asset_rows = [
        {
            "id": uuid4(),
            "account_id": account_id,
            "product_id": product,
            "name": name,
            "kind": kind,
            "status": status,
            "automatic": automatic,
        }
        for product, name, kind, status, automatic in (
            (product_id, "Manual product", "product", "active", False),
            (product_id, "Paused automatic product", "product", "paused", True),
            (product_id, "Historical manual ad", "ad", "ended", False),
            (None, "Draft ad without product", "ad", "draft", False),
        )
    ]

    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_asset_history_migration_fixture"))
            connection.execute(
                text("SET LOCAL search_path TO partner_asset_history_migration_fixture")
            )
            metadata.create_all(connection)
            if populated:
                connection.execute(metadata.tables["partner_accounts"].insert(), {"id": account_id})
                connection.execute(metadata.tables["partner_products"].insert(), {"id": product_id})
                connection.execute(assets.insert(), asset_rows)
                connection.execute(
                    deliveries.insert(),
                    [
                        {
                            "delivery_id": uuid4(),
                            "asset_id": asset["id"],
                            "issued_at": datetime(2026, 10, 13, 12, tzinfo=UTC),
                        }
                        for asset in asset_rows
                    ],
                )
            before = {
                table.name: connection.execute(select(table).order_by(*table.primary_key)).all()
                for table in (assets, deliveries)
            }
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
                assets.indexes.add(history_index)
                assert compare_metadata(context, metadata) == []
                index = next(
                    item
                    for item in inspect(connection).get_indexes("partner_assets")
                    if item["name"] == "ix_partner_asset_product"
                )
                assert index["column_names"] == ["product_id", "id"]
                assert index["unique"] is False
                assert index.get("dialect_options", {}).get("postgresql_where") is None
                if populated:
                    assert set(
                        connection.scalars(
                            select(assets.c.id).where(assets.c.product_id == product_id)
                        )
                    ) == {asset["id"] for asset in asset_rows[:3]}
                for table in (assets, deliveries):
                    assert (
                        connection.execute(select(table).order_by(*table.primary_key)).all()
                        == before[table.name]
                    )

                migration.downgrade()
                assets.indexes.remove(history_index)
                assert compare_metadata(context, metadata) == []
                assert "ix_partner_asset_product" not in {
                    item["name"] for item in inspect(connection).get_indexes("partner_assets")
                }
                for table in (assets, deliveries):
                    assert (
                        connection.execute(select(table).order_by(*table.primary_key)).all()
                        == before[table.name]
                    )
        finally:
            transaction.rollback()
