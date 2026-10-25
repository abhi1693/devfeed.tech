"""The branch's partnership schema upgrades from the unchanged master schema."""

import importlib
import random

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from devfeed_core.models import Base
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
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.schema import CreateTable

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT


def test_partnership_revision_follows_master_schema():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [SCHEMA_REVISION] == ["0041"]
    assert scripts.get_revision("0037").down_revision == "0036"
    assert scripts.get_revision("0038").down_revision == "0037"
    assert scripts.get_revision("0039").down_revision == "0038"
    assert scripts.get_revision("0040").down_revision == "0039"
    assert scripts.get_revision("0041").down_revision == "0040"
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
        "0041",
        "0040",
        "0039",
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
        (id,name,product_url,tagline,pricing,technologies,evidence,revision,status,
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
    # The display-copy rename must preserve change capture on the registered
    # database column, including raw writes that bypass the API validators.
    savepoint = connection.begin_nested()
    try:
        connection.execute(
            text("UPDATE partner_products SET tagline=tagline WHERE id=:product"), identifiers
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
            == generation + 1
        )
        connection.execute(
            text("UPDATE partner_products SET tagline=:tagline WHERE id=:product"),
            {**identifiers, "tagline": "x" * 160},
        )
        assert (
            connection.scalar(
                text("SELECT updated_at FROM partner_products WHERE id=:product"), identifiers
            )
            != current_updated_at
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
        with pytest.raises(DataError), connection.begin_nested():
            connection.execute(
                text("UPDATE partner_products SET tagline=:tagline WHERE id=:product"),
                {**identifiers, "tagline": "x" * 161},
            )
    finally:
        savepoint.rollback()
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


def expected_metadata():
    """Model parity includes the existing tables changed by partnership matching."""
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
    for name, table in Base.metadata.tables.items():
        if name.startswith("partner_"):
            table.to_metadata(metadata)
    return metadata


def create_master_prerequisites(connection, metadata):
    """Only unchanged prerequisite columns are needed in the isolated transaction."""
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
    connection.execute(text("CREATE TABLE sources (id uuid PRIMARY KEY)"))
    connection.execute(CreateTable(metadata.tables["topic_relations"]))
    source_logos = importlib.import_module("migrations.versions.0023_managed_source_logos")
    connection.execute(
        text(
            """CREATE TABLE article_image_jobs (
        id uuid PRIMARY KEY, article_id uuid, topic_id uuid, source_id uuid,
        operation text, status text, created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_image_job_subject CHECK ("""
            + source_logos.NEW_SUBJECT
            + "))"
        )
    )
    connection.execute(text("CREATE TABLE existing_data (value text NOT NULL)"))
    connection.execute(text("INSERT INTO existing_data VALUES ('keep me')"))


def seed_master_data(connection):
    # Existing publisher text can contain words larger than GIN's entry limit.
    long_word = "".join(random.Random(0).choices("abcdefghijklmnopqrstuvwxyz0123456789", k=10_000))
    summary = "Observe Python applications and metrics. " + long_word
    connection.execute(
        text("""INSERT INTO articles(id,title,summary,content_type,publication_status)
        VALUES('88888888-8888-4888-8888-888888888888','Python tutorial',:summary,
        'tutorial','published')"""),
        {"summary": summary},
    )
    connection.execute(
        text("""INSERT INTO topics(id,name,aliases)
        VALUES('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','Python runtime',ARRAY['Python'])""")
    )
    connection.execute(
        text("""INSERT INTO sources(id)
        VALUES('77777777-7777-4777-8777-777777777777')""")
    )
    for identifier, column, subject, operation in (
        (
            "11111111-1111-4111-8111-111111111111",
            "article_id",
            "88888888-8888-4888-8888-888888888888",
            "discover",
        ),
        (
            "22222222-2222-4222-8222-222222222222",
            "topic_id",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "topic-logo",
        ),
        (
            "33333333-3333-4333-8333-333333333333",
            "source_id",
            "77777777-7777-4777-8777-777777777777",
            "source-logo",
        ),
    ):
        connection.execute(
            text(f"""INSERT INTO article_image_jobs(id,{column},operation,status)
            VALUES(:id,:subject,:operation,'queued')"""),
            {"id": identifier, "subject": subject, "operation": operation},
        )
    return summary


def master_snapshot(connection):
    return {
        name: connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
        for name in ("articles", "topics", "sources", "article_image_jobs")
    }


def assert_master_rows_preserved(connection, before):
    for name, rows in before.items():
        for row in rows:
            current = (
                connection.execute(text(f"SELECT * FROM {name} WHERE id=:id"), {"id": row["id"]})
                .mappings()
                .one()
            )
            assert all(current[key] == value for key, value in row.items())


def assert_partner_schema(connection, metadata):
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_server_default": True,
            "include_name": lambda name, kind, parent: kind != "table" or name in metadata.tables,
        },
    )
    assert compare_metadata(context, metadata) == []
    inspector = inspect(connection)
    for name, table in metadata.tables.items():
        expected_checks = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert {check["name"] for check in inspector.get_check_constraints(name)} == expected_checks
    assert "frequency_cap" not in {
        column["name"] for column in inspector.get_columns("partner_accounts")
    }
    for relation in ("partner_products", "partner_listings"):
        columns = {column["name"]: column for column in inspector.get_columns(relation)}
        assert "description" not in columns
        assert columns["tagline"]["type"].length == 160
        assert columns["tagline"]["nullable"] is False
    assert "partner_match_states" not in inspector.get_table_names()
    assert inspector.get_pk_constraint("partner_delivery_events")["constrained_columns"] == [
        "asset_id",
        "delivery_id",
        "kind",
    ]
    image_index = next(
        index
        for index in inspector.get_indexes("article_image_jobs")
        if index["name"] == "uq_product_image_active"
    )
    assert image_index["column_names"] == ["product_id", "operation"]
    assert image_index["unique"] is True
    custom_index = next(
        index
        for index in inspector.get_indexes("article_image_jobs")
        if index["name"] == "uq_partner_ad_image_active"
    )
    assert custom_index["column_names"] == ["account_id", "product_id"]
    assert custom_index["unique"] is True
    assert {column["name"] for column in inspector.get_columns("article_image_jobs")} >= {
        "account_id",
        "subject_revision",
    }


def assert_enrollment_and_image_jobs(connection):
    connection.execute(
        text("""INSERT INTO partner_accounts(id,name,tier,status,created_at)
        VALUES('22222222-2222-4222-8222-222222222222','New partner','bronze','active',now()),
        ('dddddddd-dddd-4ddd-8ddd-dddddddddddd','Enrollment partner','gold','active',now())""")
    )
    assert connection.execute(
        text("""SELECT organic_enabled,auto_include_products,
        promotion_surfaces,ad_formats,audience FROM partner_accounts
        WHERE id='22222222-2222-4222-8222-222222222222'""")
    ).one() == (
        True,
        True,
        ["article", "topic", "feed", "search", "header"],
        ["feed"],
        {},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("""UPDATE partner_accounts SET promotion_kind='ad'
            WHERE id='22222222-2222-4222-8222-222222222222'""")
        )
    connection.execute(
        text("""INSERT INTO partner_products
        (id,name,product_url,tagline,pricing,technologies,evidence,revision,status,
        verified_at,updated_at,reviews)
        VALUES('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee','Go observer',
        'https://observer.example/','Observe Go tasks.','paid','["Go"]','[]',1,
        'approved',now(),now(),'[]')""")
    )
    connection.execute(
        text("""INSERT INTO partner_promotion_enrollments(account_id,product_cursor)
        VALUES('dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee')""")
    )
    connection.execute(
        text("""DELETE FROM partner_products
        WHERE id='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'""")
    )
    enrollment = connection.execute(text("SELECT * FROM partner_promotion_enrollments")).one()
    assert str(enrollment.product_cursor) == "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    assert enrollment.requested_at is not None and enrollment.available_at is not None
    assert enrollment.sweep_started_at is None
    connection.execute(
        text("""DELETE FROM partner_accounts
        WHERE id='dddddddd-dddd-4ddd-8ddd-dddddddddddd'""")
    )
    assert connection.scalar(text("SELECT count(*) FROM partner_promotion_enrollments")) == 0

    # Both product operations may be active, while another job for one operation cannot.
    product = connection.scalar(text("SELECT id FROM partner_products LIMIT 1"))
    connection.execute(
        text("""INSERT INTO partner_assets
            (id,account_id,product_id,name,kind,status,created_at,automatic)
            VALUES('99999999-9999-4999-8999-999999999999',
            '22222222-2222-4222-8222-222222222222',:product,
            'Automatic product','product','active',now(),true)"""),
        {"product": product},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("""UPDATE partner_assets SET automatic=false
            WHERE id='99999999-9999-4999-8999-999999999999'""")
        )
    for kind in ("impression", "click"):
        connection.execute(
            text("""INSERT INTO partner_delivery_events
            (asset_id,delivery_id,kind,day) VALUES
            ('99999999-9999-4999-8999-999999999999',
            'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',:kind,current_date)"""),
            {"kind": kind},
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("""INSERT INTO partner_delivery_events
            (asset_id,delivery_id,kind,day) VALUES
            ('99999999-9999-4999-8999-999999999999',
            'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','impression',current_date)""")
        )
    for identifier, operation in (
        ("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "product-logo"),
        ("ffffffff-ffff-4fff-8fff-ffffffffffff", "product-image"),
    ):
        connection.execute(
            text("""INSERT INTO article_image_jobs(id,product_id,operation,status)
            VALUES(:id,:product,:operation,'queued')"""),
            {"id": identifier, "product": product, "operation": operation},
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("""INSERT INTO article_image_jobs(id,product_id,operation,status)
            VALUES('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',:product,'product-logo','running')"""),
            {"product": product},
        )
    connection.execute(
        text("""INSERT INTO partner_accounts(id,name,tier,status,created_at)
        VALUES('dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        'Another image owner','gold','active',now())""")
    )
    for account in (
        "22222222-2222-4222-8222-222222222222",
        "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    ):
        connection.execute(
            text("""INSERT INTO article_image_jobs
            (id,account_id,product_id,subject_revision,operation,status)
            VALUES(gen_random_uuid(),:account,:product,1,'product-ad-image','queued')"""),
            {"account": account, "product": product},
        )
    for account, revision in ((None, 1), ("22222222-2222-4222-8222-222222222222", None)):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("""INSERT INTO article_image_jobs
                (id,account_id,product_id,subject_revision,operation,status)
                VALUES(gen_random_uuid(),:account,:product,:revision,'product-ad-image','queued')"""),
                {"account": account, "product": product, "revision": revision},
            )


@pytest.mark.integration
@pytest.mark.parametrize("populated", [False, True], ids=["fresh", "master-data"])
def test_consolidated_migration_matches_models_and_downgrades_cleanly(database, populated):
    migration = importlib.import_module("migrations.versions.0024_partnerships")
    metadata = expected_metadata()
    partner_names = {name for name in metadata.tables if name.startswith("partner_")}
    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO partner_migration_fixture"))
            create_master_prerequisites(connection, metadata)
            summary = seed_master_data(connection) if populated else None
            before = master_snapshot(connection)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                assert_partner_schema(connection, metadata)
                assert_master_rows_preserved(connection, before)
                if populated:
                    assert connection.scalar(text("SELECT summary FROM articles")) == summary
                    assert "python" in connection.scalar(
                        text("SELECT matching_tokens FROM articles")
                    )
                    assert "python" in connection.scalar(text("SELECT matching_tokens FROM topics"))
                assert_matching_change_capture(connection)
                assert_enrollment_and_image_jobs(connection)
                connection.execute(
                    text("""UPDATE articles SET sponsor_names='["Sponsor"]'
                    WHERE id='55555555-5555-4555-8555-555555555555'""")
                )
                migration.downgrade()
                assert connection.execute(
                    text("""SELECT publication_status,editorial_revision
                    FROM articles WHERE id='55555555-5555-4555-8555-555555555555'""")
                ).one() == (
                    "unpublished",
                    1,
                )
                assert not partner_names.intersection(inspect(connection).get_table_names())
                assert connection.scalar(text("SELECT value FROM existing_data")) == "keep me"
                assert_master_rows_preserved(connection, before)
                assert connection.scalar(text("SELECT count(*) FROM article_image_jobs")) == len(
                    before["article_image_jobs"]
                )
                assert "product_id" not in {
                    column["name"]
                    for column in inspect(connection).get_columns("article_image_jobs")
                }
                for table in ("articles", "topics"):
                    assert "matching_tokens" not in {
                        column["name"] for column in inspect(connection).get_columns(table)
                    }
                assert "sponsor_names" not in {
                    column["name"] for column in inspect(connection).get_columns("articles")
                }
                assert (
                    connection.scalar(
                        text("""SELECT count(*) FROM pg_proc
                    WHERE pronamespace=current_schema()::regnamespace
                    AND (proname LIKE 'partner_matching_%'
                    OR proname='preserve_partner_automatic_asset_identity')""")
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text("""SELECT count(*) FROM pg_trigger t
                    JOIN pg_class c ON c.oid=t.tgrelid
                    WHERE c.relnamespace=current_schema()::regnamespace
                    AND tgname LIKE 'partner_matching_%'""")
                    )
                    == 0
                )
                migration.upgrade()
                assert_partner_schema(connection, metadata)
                assert_master_rows_preserved(connection, before)
        finally:
            transaction.rollback()
