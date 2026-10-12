"""Partnership migrations match the models and preserve existing data."""

import importlib.util
import random
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
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable
from test_partner_promotions import seed_catalog

ROOT = Path(__file__).parents[1]


def test_partnership_revisions_follow_master_schema():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [SCHEMA_REVISION] == ["0038"]
    assert scripts.get_revision("0038").down_revision == "0037"
    assert scripts.get_revision("0037").down_revision == "0036"
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
        "0038",
        "0037",
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


def assert_matching_change_capture(connection):
    """Raw SQL changes share the transaction with projections and durable work."""
    identifiers = {
        "product": "44444444-4444-4444-8444-444444444444",
        "article": "55555555-5555-4555-8555-555555555555",
        "topic": "66666666-6666-4666-8666-666666666666",
    }
    connection.execute(
        text("""INSERT INTO topics(id,name,aliases)
        VALUES(:topic,'Python runtime',ARRAY['Python'])"""),
        identifiers,
    )
    connection.execute(
        text("""INSERT INTO partner_products
        (id,name,product_url,description,pricing,technologies,evidence,revision,status,
        verified_at,updated_at,reviews)
        VALUES(:product,'Python observer','https://observer.example/','Observe Python tasks.',
        'paid','["Python"]','[]',1,'approved',now(),now(),'[]')"""),
        identifiers,
    )
    connection.execute(
        text("""INSERT INTO articles(id,title,summary,content_type,publication_status)
        VALUES(:article,'Python tutorial','Observe Python applications and inspect metrics.',
        'tutorial','published')"""),
        identifiers,
    )
    assert "python" in connection.scalar(
        text("SELECT matching_tokens FROM partner_products WHERE id=:product"), identifiers
    )
    assert "python" in connection.scalar(
        text("SELECT matching_tokens FROM articles WHERE id=:article"), identifiers
    )
    assert "python" in connection.scalar(
        text("SELECT matching_tokens FROM topics WHERE id=:topic"), identifiers
    )
    generation = connection.scalar(
        text(
            "SELECT generation FROM partner_match_jobs WHERE kind='product' AND entity_id=:product"
        ),
        identifiers,
    )
    assert generation is not None
    assert (
        connection.scalar(
            text(
                "SELECT generation FROM partner_match_jobs "
                "WHERE kind='article' AND entity_id=:article"
            ),
            identifiers,
        )
        is not None
    )
    connection.execute(
        text(
            "UPDATE partner_match_jobs SET cursor=:article "
            "WHERE kind='product' AND entity_id=:product"
        ),
        identifiers,
    )
    connection.execute(text("UPDATE partner_products SET name=name WHERE id=:product"), identifiers)
    assert (
        connection.scalar(
            text(
                "SELECT generation FROM partner_match_jobs "
                "WHERE kind='product' AND entity_id=:product"
            ),
            identifiers,
        )
        == generation
    )
    previous_updated_at = connection.scalar(
        text("SELECT updated_at FROM partner_products WHERE id=:product"), identifiers
    )
    connection.execute(
        text("UPDATE partner_products SET name='Current Python observer' WHERE id=:product"),
        identifiers,
    )
    current_updated_at = connection.scalar(
        text("SELECT updated_at FROM partner_products WHERE id=:product"), identifiers
    )
    assert current_updated_at != previous_updated_at
    assert connection.execute(
        text(
            "SELECT generation,cursor FROM partner_match_jobs "
            "WHERE kind='product' AND entity_id=:product"
        ),
        identifiers,
    ).one() == (generation + 1, None)
    connection.execute(
        text("""UPDATE partner_products SET status='paused',verified_at=NULL,excluded=true
        WHERE id=:product"""),
        identifiers,
    )
    assert (
        connection.scalar(
            text("SELECT updated_at FROM partner_products WHERE id=:product"), identifiers
        )
        == current_updated_at
    )
    assert (
        connection.scalar(
            text(
                "SELECT generation FROM partner_match_jobs "
                "WHERE kind='product' AND entity_id=:product"
            ),
            identifiers,
        )
        == generation + 2
    )
    connection.execute(
        text("""INSERT INTO partner_product_topics
        (product_id,topic_id,product_revision,product_updated_at,topic_updated_at,reason,score,updated_at)
        SELECT p.id,t.id,p.revision,p.updated_at,t.updated_at,'Prepared fixture',100,now()
        FROM partner_products p,topics t WHERE p.id=:product AND t.id=:topic"""),
        identifiers,
    )
    connection.execute(text("DELETE FROM partner_match_jobs"))
    savepoint = connection.begin_nested()
    try:
        connection.execute(
            text("UPDATE topics SET aliases=ARRAY['Rust'] WHERE id=:topic"), identifiers
        )
        assert "rust" in connection.scalar(
            text("SELECT matching_tokens FROM topics WHERE id=:topic"), identifiers
        )
        assert connection.scalar(text("SELECT count(*) FROM partner_product_topics")) == 0
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM partner_match_jobs "
                    "WHERE kind='topics' AND entity_id=:product"
                ),
                identifiers,
            )
            == 1
        )
    finally:
        savepoint.rollback()
    assert connection.scalar(text("SELECT count(*) FROM partner_product_topics")) == 1
    assert connection.scalar(text("SELECT count(*) FROM partner_match_jobs")) == 0


def assert_matching_bootstrap_preserves_history(connection, migration):
    """Unknown old topic dependencies are rebuilt without losing durable decisions."""
    # Valid publisher text can contain an incompressible word exceeding GIN's
    # entry limit. Backfill and index construction must accept existing text.
    long_word = "".join(random.Random(0).choices("abcdefghijklmnopqrstuvwxyz0123456789", k=10_000))
    summary = "Observe Python applications and metrics. " + long_word
    identifiers = {
        "product": "77777777-7777-4777-8777-777777777777",
        "positive_article": "88888888-8888-4888-8888-888888888888",
        "negative_article": "99999999-9999-4999-8999-999999999999",
        "topic": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "asset": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "delivery": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    }
    connection.execute(
        text("""INSERT INTO partner_products
        (id,name,product_url,description,pricing,technologies,evidence,revision,status,
        verified_at,updated_at,reviews)
        VALUES(:product,'Legacy Python observer','https://legacy-observer.example/',
        'Observe Python tasks.','paid','["Python"]','[]',1,'approved',now(),now(),'[]')"""),
        identifiers,
    )
    connection.execute(
        text("""INSERT INTO articles(id,title,summary,content_type,publication_status)
        VALUES(:positive_article,'Python tutorial',:summary,'tutorial','published'),
        (:negative_article,'Unrelated tutorial','Inspect unrelated production services.',
        'tutorial','published')"""),
        {**identifiers, "summary": summary},
    )
    connection.execute(
        text("""INSERT INTO topics(id,name,aliases)
        VALUES(:topic,'Current runtime',ARRAY['Current'])"""),
        identifiers,
    )
    # An old removed alias can leave a plausible edge even when both stored
    # timestamps still equal their targets. Bootstrap must not trust that edge.
    connection.execute(
        text("""INSERT INTO partner_product_topics
        (product_id,topic_id,product_revision,product_updated_at,topic_updated_at,reason,score,updated_at)
        SELECT p.id,t.id,p.revision,p.updated_at,t.updated_at,'Removed alias dependency',100,now()
        FROM partner_products p,topics t WHERE p.id=:product AND t.id=:topic"""),
        identifiers,
    )
    connection.execute(
        text("""INSERT INTO partner_article_matches
        (product_id,article_id,product_revision,product_updated_at,editorial_revision,
        article_fingerprint,origin,relevant,reason,score,updated_at)
        SELECT p.id,a.id,p.revision,p.updated_at,a.editorial_revision,repeat('a',64),
        'evaluation',a.id=CAST(:positive_article AS uuid),
        CASE WHEN a.id=CAST(:positive_article AS uuid) THEN 'Explicit approval'
        ELSE 'Explicit rejection' END,
        CASE WHEN a.id=CAST(:positive_article AS uuid) THEN 100 ELSE 0 END,now()
        FROM partner_products p,articles a
        WHERE p.id=:product AND a.id IN (:positive_article,:negative_article)"""),
        identifiers,
    )
    connection.execute(
        text("""INSERT INTO partner_assets
        (id,account_id,product_id,name,kind,status,created_at)
        VALUES(:asset,'22222222-2222-4222-8222-222222222222',:product,
        'Legacy product placement','product','active',now())"""),
        identifiers,
    )
    connection.execute(
        text("""INSERT INTO partner_placement_deliveries(delivery_id,asset_id,issued_at,surface)
        VALUES(:delivery,:asset,now(),'article')"""),
        identifiers,
    )
    snapshots = {
        "partner_article_matches": "WHERE product_id=:product ORDER BY article_id",
        "partner_assets": "WHERE id=:asset",
        "partner_placement_deliveries": "WHERE delivery_id=:delivery",
    }
    before = {
        table: connection.execute(text(f"SELECT * FROM {table} {predicate}"), identifiers)
        .mappings()
        .all()
        for table, predicate in snapshots.items()
    }
    assert len(before["partner_article_matches"]) == 2
    assert connection.scalar(text("SELECT count(*) FROM partner_product_topics")) == 1

    migration.upgrade()

    assert (
        connection.scalar(
            text("SELECT summary FROM articles WHERE id=:positive_article"), identifiers
        )
        == summary
    )
    assert "python" in connection.scalar(
        text("SELECT matching_tokens FROM articles WHERE id=:positive_article"), identifiers
    )
    assert connection.scalar(text("SELECT count(*) FROM partner_product_topics")) == 0
    assert connection.execute(
        text("SELECT kind,entity_id,generation,cursor FROM partner_match_jobs")
    ).one() == ("product", connection.scalar(text("SELECT id FROM partner_products")), 1, None)
    for table, predicate in snapshots.items():
        assert (
            connection.execute(text(f"SELECT * FROM {table} {predicate}"), identifiers)
            .mappings()
            .all()
            == before[table]
        )
    # Keep the populated upgrade isolated from the consolidated fixture's
    # subsequent checks and its independent change-capture regression.
    connection.execute(
        text("DELETE FROM partner_placement_deliveries WHERE delivery_id=:delivery"), identifiers
    )
    connection.execute(text("DELETE FROM partner_assets WHERE id=:asset"), identifiers)
    connection.execute(text("DELETE FROM partner_products WHERE id=:product"), identifiers)
    connection.execute(
        text("DELETE FROM articles WHERE id IN (:positive_article,:negative_article)"), identifiers
    )
    connection.execute(text("DELETE FROM topics WHERE id=:topic"), identifiers)
    connection.execute(text("DELETE FROM partner_match_jobs"))


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
        ("0037", "0037_partner_asset_history_index.py"),
        ("0038", "0038_partner_matching_invalidation.py"),
    ):
        spec = importlib.util.spec_from_file_location(
            f"partner_{revision}", ROOT / "migrations/versions" / filename
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        extra_migrations.append(module)
    partner_names = {name for name in Base.metadata.tables if name.startswith("partner_")}
    metadata = MetaData()
    topics = Table(
        "topics",
        metadata,
        Column("id", Uuid(), primary_key=True),
        Column("name", String(100), nullable=False, server_default=""),
        Column("aliases", ARRAY(String(100)), nullable=False, server_default="{}"),
        Column("identity_keys", ARRAY(Text), nullable=False, server_default="{}"),
        Column("status", String(20), nullable=False, server_default="active"),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
        Column("matching_tokens", ARRAY(Text), nullable=False, server_default="{}"),
    )
    articles = Table(
        "articles",
        metadata,
        Column("id", Uuid(), primary_key=True),
        Column("title", String(500), nullable=False, server_default=""),
        Column("summary", Text(), nullable=False, server_default=""),
        Column("content_type", String(30), nullable=False, server_default="article"),
        Column("publication_status", String(20), nullable=False, server_default="unpublished"),
        Column("editorial_revision", Integer(), nullable=False, server_default="0"),
        Column("sponsor_names", JSONB(), nullable=False, server_default="[]"),
        Column("matching_tokens", ARRAY(Text), nullable=False, server_default="{}"),
        CheckConstraint("jsonb_typeof(sponsor_names) = 'array'", name="ck_article_sponsor_names"),
    )
    for table in (topics, articles):
        Index(f"ix_{table.name}_matching_tokens", table.c.matching_tokens, postgresql_using="gin")
    Base.metadata.tables["topic_relations"].to_metadata(metadata)
    for name in partner_names:
        Base.metadata.tables[name].to_metadata(metadata)

    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO partner_migration_fixture"))
            connection.execute(
                text("""CREATE TABLE articles (id uuid PRIMARY KEY,
                title varchar(500) NOT NULL DEFAULT '', summary text NOT NULL DEFAULT '',
                content_type varchar(30) NOT NULL DEFAULT 'article',
                publication_status varchar(20) NOT NULL DEFAULT 'unpublished',
                editorial_revision integer NOT NULL DEFAULT 0)""")
            )
            connection.execute(
                text("""CREATE TABLE topics (id uuid PRIMARY KEY,
                name varchar(100) NOT NULL DEFAULT '',
                aliases varchar(100)[] NOT NULL DEFAULT '{}',
                identity_keys text[] NOT NULL DEFAULT '{}',
                status varchar(20) NOT NULL DEFAULT 'active',
                updated_at timestamptz NOT NULL DEFAULT now())""")
            )
            # The reverse relationship index belongs to 0038, not its prerequisite table.
            connection.execute(CreateTable(metadata.tables["topic_relations"]))
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
                        kind != "table"
                        or name in partner_names | {"articles", "topics", "topic_relations"}
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
                    if module.revision == "0038":
                        assert_matching_bootstrap_preserves_history(connection, module)
                    else:
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
                assert_matching_change_capture(connection)
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
