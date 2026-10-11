"""Product repetition is unrestricted while commercial budgets and ledgers stay intact."""

import importlib
from io import StringIO

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import PartnerCampaign, PartnerTrackedDailyMetric
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_tracking import PartnerTrackingTokens, record_delivery_event
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_partner_placements_integration import KEY
from test_partner_placements_integration import catalog as catalog_fixture

catalog = catalog_fixture
migration = importlib.import_module("migrations.versions.0032_remove_product_exposure_caps")


def test_exposure_migration_sql_only_removes_obsolete_account_setting():
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    assert output.getvalue().strip().split(";\n\n") == [
        "ALTER TABLE partner_accounts DROP CONSTRAINT ck_partner_frequency_cap",
        "ALTER TABLE partner_accounts DROP COLUMN frequency_cap;",
    ]


@pytest.mark.integration
def test_exposure_migration_roundtrip_preserves_accounts_budgets_and_metrics(database):
    with database().get_bind().connect() as connection, connection.begin():
        connection.execute(text("CREATE SCHEMA exposure_migration_fixture"))
        connection.execute(text("SET LOCAL search_path TO exposure_migration_fixture"))
        connection.execute(
            text("""CREATE TABLE partner_accounts (
                id int PRIMARY KEY, name text NOT NULL,
                frequency_cap int NOT NULL DEFAULT 3,
                CONSTRAINT ck_partner_frequency_cap CHECK (frequency_cap BETWEEN 1 AND 20))""")
        )
        connection.execute(
            text("""CREATE TABLE historical_metrics (
                account_id int REFERENCES partner_accounts(id),
                campaign_daily_budget int, impressions int, clicks int)""")
        )
        connection.execute(text("INSERT INTO partner_accounts VALUES (1,'Existing partner',20)"))
        connection.execute(text("INSERT INTO historical_metrics VALUES (1,100,73,9)"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            assert "frequency_cap" not in {
                column["name"] for column in inspect(connection).get_columns("partner_accounts")
            }
            assert not inspect(connection).get_check_constraints("partner_accounts")
            assert connection.execute(text("SELECT * FROM partner_accounts")).one() == (
                1,
                "Existing partner",
            )
            assert connection.execute(text("SELECT * FROM historical_metrics")).one() == (
                1,
                100,
                73,
                9,
            )
            connection.execute(text("INSERT INTO partner_accounts VALUES (2,'New partner')"))
            migration.downgrade()
            assert connection.execute(
                text("SELECT frequency_cap FROM partner_accounts ORDER BY id")
            ).scalars().all() == [3, 3]
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("UPDATE partner_accounts SET frequency_cap=21"))
            migration.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM partner_accounts")) == 2
            assert connection.execute(text("SELECT * FROM historical_metrics")).one() == (
                1,
                100,
                73,
                9,
            )
        connection.rollback()


@pytest.mark.integration
def test_same_reader_can_receive_product_past_twenty_appearances_until_campaign_budget(
    database, catalog
):
    with database.begin() as session:
        session.get(PartnerCampaign, catalog["campaign"]).daily_impression_limit = 25
    query = PlacementQuery(surface="topic", topic_id=catalog["topic"])
    for _ in range(25):
        with database.begin() as session:
            items = deliver_placements(session, query, KEY, user_id=catalog["user"])
            assert len(items) == 2 and len({item["product_id"] for item in items}) == 2
            assert all("frequency_cap" not in item for item in items)
            sponsored = next(item for item in items if item["sponsored"])
            assert sponsored["product_id"] == catalog["paid_product"]
            assert record_delivery_event(
                session, PartnerTrackingTokens(KEY).verify(sponsored["receipt"]), "impression"
            )
    with database.begin() as session:
        items = deliver_placements(session, query, KEY, user_id=catalog["user"])
        assert items and all(not item["sponsored"] for item in items)
        assert session.scalar(select(PartnerTrackedDailyMetric.impressions)) == 25
