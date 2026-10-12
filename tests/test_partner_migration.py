"""Partnership migrations match the models and preserve existing data."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from devfeed_core.models import (
    Article,
    Base,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerDailyMetric,
)
from devfeed_core.version import SCHEMA_REVISION
from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from test_partner_promotions import seed_catalog

ROOT = Path(__file__).parents[1]


def test_partnership_revisions_follow_master_schema():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [SCHEMA_REVISION] == ["0036"]
    assert scripts.get_revision("0036").down_revision == "0035"
    assert scripts.get_revision("0035").down_revision == "0034"
    assert scripts.get_revision("0034").down_revision == "0033"
    assert scripts.get_revision("0033").down_revision == "0032"
    assert scripts.get_revision("0032").down_revision == "0031"
    assert scripts.get_revision("0031").down_revision == "0030"
    assert scripts.get_revision("0030").down_revision == "0029"
    assert scripts.get_revision("0029").down_revision == "0028"
    assert scripts.get_revision("0028").down_revision == "0027"
    assert scripts.get_revision("0027").down_revision == "0026"
    assert scripts.get_revision("0026").down_revision == "0025"
    assert scripts.get_revision("0025").down_revision == "0024"
    assert scripts.get_revision("0024").down_revision == "0023"
    assert scripts.get_revision("0023").down_revision == "0022"
    assert [revision.revision for revision in scripts.walk_revisions("0022", "head")] == [
        "0036",
        "0035",
        "0034",
        "0033",
        "0032",
        "0031",
        "0030",
        "0029",
        "0028",
        "0027",
        "0026",
        "0025",
        "0024",
        "0023",
        "0022",
    ]


@pytest.mark.integration
def test_consolidated_migration_matches_models_and_downgrades_cleanly(database):
    path = ROOT / "migrations/versions/0024_partnerships.py"
    spec = importlib.util.spec_from_file_location("partner_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    tier_spec = importlib.util.spec_from_file_location(
        "tier_migration", ROOT / "migrations/versions/0025_partner_account_tiers.py"
    )
    tier_migration = importlib.util.module_from_spec(tier_spec)
    tier_spec.loader.exec_module(tier_migration)
    account_spec = importlib.util.spec_from_file_location(
        "connection_account_migration",
        ROOT / "migrations/versions/0026_partner_connection_account.py",
    )
    account_migration = importlib.util.module_from_spec(account_spec)
    account_spec.loader.exec_module(account_migration)
    connector_spec = importlib.util.spec_from_file_location(
        "connector_migration", ROOT / "migrations/versions/0027_partner_connector_definitions.py"
    )
    connector_migration = importlib.util.module_from_spec(connector_spec)
    connector_spec.loader.exec_module(connector_migration)
    promotion_spec = importlib.util.spec_from_file_location(
        "promotion_migration", ROOT / "migrations/versions/0028_partner_promotions.py"
    )
    promotion_migration = importlib.util.module_from_spec(promotion_spec)
    promotion_spec.loader.exec_module(promotion_migration)
    logo_spec = importlib.util.spec_from_file_location(
        "product_logo_migration", ROOT / "migrations/versions/0029_partner_product_logos.py"
    )
    logo_migration = importlib.util.module_from_spec(logo_spec)
    logo_spec.loader.exec_module(logo_migration)
    extra_migrations = []
    for revision, filename in (
        ("0030", "0030_partner_product_images.py"),
        ("0031", "0031_partner_commercial_policy.py"),
        ("0032", "0032_remove_product_exposure_caps.py"),
        ("0033", "0033_partner_audiences.py"),
        ("0034", "0034_partner_ad_formats.py"),
        ("0035", "0035_partner_promotion_enrollment.py"),
        ("0036", "0036_partner_delivery_event_retention.py"),
    ):
        spec = importlib.util.spec_from_file_location(
            f"partner_{revision}", ROOT / "migrations/versions" / filename
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        extra_migrations.append(module)
    partner_names = {name for name in Base.metadata.tables if name.startswith("partner_")}
    metadata = MetaData()
    Table("topics", metadata, Column("id", Uuid(), primary_key=True))
    Table(
        "articles",
        metadata,
        Column("id", Uuid(), primary_key=True),
        Column("publication_status", String(20), nullable=False, server_default="unpublished"),
        Column("editorial_revision", Integer(), nullable=False, server_default="0"),
        Column("sponsor_names", JSONB(), nullable=False, server_default="[]"),
        CheckConstraint("jsonb_typeof(sponsor_names) = 'array'", name="ck_article_sponsor_names"),
    )
    for name in partner_names:
        Base.metadata.tables[name].to_metadata(metadata)

    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO partner_migration_fixture"))
            connection.execute(
                text("""CREATE TABLE articles (id uuid PRIMARY KEY,
                publication_status varchar(20) NOT NULL DEFAULT 'unpublished',
                editorial_revision integer NOT NULL DEFAULT 0)""")
            )
            connection.execute(text("CREATE TABLE topics (id uuid PRIMARY KEY)"))
            connection.execute(
                text("""CREATE TABLE article_image_jobs (
                id uuid PRIMARY KEY, article_id uuid, topic_id uuid, source_id uuid,
                operation text, status text,
                CONSTRAINT ck_image_job_subject CHECK (true))""")
            )
            connection.execute(text("CREATE TABLE existing_data (value text NOT NULL)"))
            connection.execute(text("INSERT INTO existing_data VALUES ('keep me')"))
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_server_default": True,
                    "include_name": lambda name, kind, parent: (
                        kind != "table" or name in partner_names | {"articles", "topics"}
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
                connection.execute(
                    text("""INSERT INTO partner_accounts(id,name,tier,status,created_at)
                    VALUES('22222222-2222-4222-8222-222222222222',
                    'Existing partner','bronze','active',now())""")
                )
                connection.execute(
                    text("""INSERT INTO partner_assets(id,account_id,name,kind,status,created_at)
                    VALUES('33333333-3333-4333-8333-333333333333',
                    '22222222-2222-4222-8222-222222222222','Legacy ad','ad','active',now())""")
                )
                promotion_migration.upgrade()
                logo_migration.upgrade()
                for module in extra_migrations:
                    module.upgrade()
                assert connection.execute(
                    text("""SELECT organic_enabled, auto_include_products,
                    promotion_surfaces FROM partner_accounts""")
                ).one() == (True, True, ["article", "topic", "feed"])
                assert connection.execute(
                    text("SELECT automatic,campaign_id FROM partner_assets")
                ).one() == (False, None)
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
                for module in reversed(extra_migrations):
                    module.downgrade()
                logo_migration.downgrade()
                promotion_migration.downgrade()
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
                promotion_migration.upgrade()
                logo_migration.upgrade()
                for module in extra_migrations:
                    module.upgrade()
                assert compare_metadata(context, metadata) == []
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_native_format_downgrade_preserves_ledger_and_stops_undisclosed_delivery(database):
    now = datetime.now(UTC)
    with database.begin() as session:
        account = PartnerAccount(
            name="Silver sponsor",
            tier="silver",
            promotion_kind="ad",
            ad_formats=["feed", "engagement"],
        )
        session.add(account)
        session.flush()
        _, product_id = seed_catalog(session, account.id)
        article = Article(
            canonical_url="https://publisher.example/sponsored",
            url_hash="f" * 64,
            slug="sponsored-downgrade",
            title="Sponsored article",
            publication_status="published",
            review_status="approved",
            sponsor_names=[account.name],
        )
        session.add(article)
        session.flush()
        assets = []
        for ad_format in ("feed", "engagement", "homepage_sponsorship", "sponsored_content"):
            campaign = PartnerCampaign(
                account_id=account.id,
                name=ad_format,
                status="active",
                ad_format=ad_format,
                article_id=article.id if ad_format == "sponsored_content" else None,
                managed=ad_format != "sponsored_content",
                surfaces=["feed"],
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=1),
            )
            session.add(campaign)
            session.flush()
            asset = PartnerAsset(
                account_id=account.id,
                product_id=product_id,
                campaign_id=campaign.id,
                name=ad_format,
                kind="ad",
                status="active",
                automatic=True,
                ad_format=ad_format,
                article_id=campaign.article_id,
            )
            session.add(asset)
            session.flush()
            assets.append(asset.id)
            session.add(
                PartnerDailyMetric(asset_id=asset.id, day=now.date(), impressions=100, clicks=5)
            )
        account_id, article_id, feed_asset = account.id, article.id, assets[0]

    spec = importlib.util.spec_from_file_location(
        "native_formats_migration", ROOT / "migrations/versions/0034_partner_ad_formats.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert connection.execute(
                    text("SELECT status,promotion_kind FROM partner_accounts WHERE id=:id"),
                    {"id": account_id},
                ).one() == ("paused", None)
                assert (
                    connection.scalar(
                        text("SELECT publication_status FROM articles WHERE id=:id"),
                        {"id": article_id},
                    )
                    == "unpublished"
                )
                assert set(connection.scalars(text("SELECT id FROM partner_assets"))) == set(assets)
                assert connection.execute(
                    text("SELECT sum(impressions),sum(clicks) FROM partner_daily_metrics")
                ).one() == (400, 20)
                assert (
                    connection.scalar(text("SELECT count(*) FROM partner_assets WHERE automatic"))
                    == 1
                )
                assert (
                    connection.scalar(text("SELECT count(*) FROM partner_campaigns WHERE managed"))
                    == 1
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM partner_assets WHERE status='paused'")
                    )
                    == 3
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text("UPDATE partner_assets SET automatic=false WHERE id=:id"),
                        {"id": feed_asset},
                    )
                migration.upgrade()
                assert (
                    connection.scalar(
                        text("SELECT sponsor_names FROM articles WHERE id=:id"), {"id": article_id}
                    )
                    == []
                )
                assert (
                    connection.scalar(
                        text("SELECT status FROM partner_accounts WHERE id=:id"), {"id": account_id}
                    )
                    == "paused"
                )
                assert connection.scalar(text("SELECT count(*) FROM partner_assets")) == 4
        finally:
            transaction.rollback()
