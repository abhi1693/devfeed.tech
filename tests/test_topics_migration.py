"""Exercise the actual consolidation against populated legacy tables in an isolated schema."""

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core import analysis
from devfeed_core.db import get_engine
from devfeed_core.models import Article, ArticleTopic, utcnow
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def migration(name, connection):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


@pytest.fixture
def legacy_schema(integration_environment):
    # Roll back the entire schema, including DDL, after each scenario.
    with get_engine().connect() as connection:
        transaction = connection.begin()
        schema = "topic_migration_" + uuid.uuid4().hex
        connection.execute(sa.text(f"CREATE SCHEMA {schema}"))
        connection.execute(sa.text(f"SET LOCAL search_path TO {schema}"))
        for name in (
            "0001_initial_schema",
            "0002_notification_deliveries",
            "0003_category_proposals",
        ):
            migration(name, connection).upgrade()
        metadata = sa.MetaData()
        metadata.reflect(connection)
        yield connection, metadata.tables
        transaction.rollback()


def test_category_consolidation_preserves_assignments_identity_reviews_and_audit(legacy_schema):
    connection, tables = legacy_schema
    root, child, canonical, article, tag, proposal = [uuid.uuid4() for _ in range(6)]
    connection.execute(
        tables["topics"]
        .insert()
        .values(
            id=canonical,
            name="PostgreSQL",
            slug="postgresql",
            kind="database",
            aliases=["postgres"],
            status="active",
            facts=[],
            created_at=utcnow(),
            updated_at=utcnow(),
            description="Curated database description",
        )
    )
    connection.execute(
        tables["categories"]
        .insert()
        .values(id=root, name="Databases", slug="databases", keywords=["database"])
    )
    connection.execute(
        tables["categories"]
        .insert()
        .values(
            id=child,
            name="Postgres",
            slug="postgres",
            keywords=["sql"],
            parent_id=root,
            topic_id=canonical,
            description="Legacy category description",
        )
    )
    connection.execute(
        tables["articles"]
        .insert()
        .values(
            id=article,
            canonical_url="https://example.com/db",
            url_hash="a" * 64,
            title="Database systems",
            summary="",
            content_type="article",
            feed_at=utcnow(),
            discovered_at=utcnow(),
        )
    )
    connection.execute(
        tables["article_categories"]
        .insert()
        .values(article_id=article, category_id=child, origin="manual")
    )
    connection.execute(
        tables["tags"]
        .insert()
        .values(id=tag, name="Storage", slug="storage", aliases=[], category_id=root)
    )
    connection.execute(
        tables["article_tags"].insert().values(article_id=article, tag_id=tag, origin="heuristic")
    )
    actor = {"subject": "original-admin", "issuer": "example", "organization_id": "org"}
    original = dict(
        name="Security",
        slug="security",
        keywords=["security"],
        parent_slug=None,
        topic_slug=None,
        description=None,
    )
    connection.execute(
        tables["category_proposals"]
        .insert()
        .values(
            id=proposal,
            batch_id=uuid.uuid4(),
            slug="security",
            action="create",
            origin="import",
            source_name="curated.json",
            proposed=original,
            evidence=[{"row": 1}],
            status="pending",
            created_at=utcnow(),
            created_by=actor,
        )
    )
    migration("0004_topics_ssot", connection).upgrade()
    names = set(sa.inspect(connection).get_table_names())
    assert not {"categories", "article_categories", "category_proposals"} & names
    assert "category_id" not in {c["name"] for c in sa.inspect(connection).get_columns("tags")}
    topics = {r.slug: r for r in connection.execute(sa.text("SELECT * FROM topics"))}
    assert set(topics) == {"databases", "postgresql"}
    assert topics["postgresql"].id == canonical
    assert topics["postgresql"].description == "Curated database description"
    assert topics["postgresql"].keywords == ["sql"]
    links = connection.execute(
        sa.text("SELECT topic_id, origin FROM article_topics WHERE article_id=:id"), {"id": article}
    ).all()
    assert set(links) == {(canonical, "manual"), (root, "heuristic")}
    assert connection.execute(
        sa.text("SELECT topic_id, related_topic_id FROM topic_relations WHERE relation='part_of'")
    ).one() == (canonical, root)
    assert connection.scalar(sa.text("SELECT topic_id FROM tags WHERE id=:id"), {"id": tag}) == root
    review = connection.execute(sa.text("SELECT * FROM topic_proposals")).one()
    assert review.id == proposal and review.status == "pending" and review.created_by == actor
    assert review.proposed["kind"] == "discipline"
    assert (
        connection.scalar(
            sa.text(
                "SELECT record->'proposed' FROM taxonomy_migration_archive "
                "WHERE source_table='category_proposals'"
            )
        )
        == original
    )


def test_ambiguous_category_identity_aborts_without_guessing(legacy_schema):
    connection, tables = legacy_schema
    for name, slug, aliases in [("One", "one", ["shared"]), ("Two", "two", [])]:
        connection.execute(
            tables["topics"]
            .insert()
            .values(
                id=uuid.uuid4(),
                name=name,
                slug=slug,
                kind="discipline",
                aliases=aliases,
                status="active",
                facts=[],
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    connection.execute(
        tables["categories"]
        .insert()
        .values(id=uuid.uuid4(), name="Shared", slug="two", keywords=[])
    )
    with pytest.raises(RuntimeError, match="overlapping topic identities"):
        migration("0004_topics_ssot", connection).upgrade()


@pytest.mark.parametrize("category_count", [240, 261])
def test_combined_catalog_preflight_counts_resolved_active_identities(
    legacy_schema, category_count
):
    connection, tables = legacy_schema
    topics = [
        dict(
            id=uuid.uuid4(),
            name=f"Topic {i}",
            slug=f"topic-{i}",
            kind="technology",
            status="active" if i < 261 else "proposed",
            aliases=[],
            facts=[],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        for i in range(270)
    ]
    connection.execute(tables["topics"].insert(), topics)
    connection.execute(
        tables["categories"].insert(),
        [
            dict(
                id=uuid.uuid4(),
                name=f"Category {i}",
                slug=f"category-{i}",
                keywords=[],
                topic_id=topics[0]["id"] if i == 0 else None,
            )
            for i in range(category_count)
        ],
    )
    revision = migration("0004_topics_ssot", connection)
    if category_count == 261:
        with pytest.raises(RuntimeError, match="521 active topics.*at most 500.*before retrying"):
            revision.upgrade()
        assert connection.scalar(sa.text("SELECT count(*) FROM topics")) == 270
        assert connection.scalar(sa.text("SELECT count(*) FROM categories")) == 261
        assert "taxonomy_migration_archive" not in sa.inspect(connection).get_table_names()
        assert "keywords" not in {c["name"] for c in sa.inspect(connection).get_columns("topics")}
    else:
        revision.upgrade()
        with Session(bind=connection) as session:
            assert len(analysis.catalog(session)["topics"]) == 500


def assignment_scenario(connection, tables, role, origin):
    topic_id, article_id, category_id = [uuid.uuid4() for _ in range(3)]
    connection.execute(
        tables["topics"]
        .insert()
        .values(
            id=topic_id,
            name="Angular",
            slug="angular",
            kind="framework",
            status="active",
            aliases=[],
            facts=[],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    connection.execute(
        tables["categories"]
        .insert()
        .values(
            id=category_id,
            name="Angular",
            slug="angular",
            keywords=[],
            topic_id=topic_id,
        )
    )
    connection.execute(
        tables["articles"]
        .insert()
        .values(
            id=article_id,
            canonical_url="https://example.com/angular",
            url_hash="a" * 64,
            title="Angular routing",
            summary="Angular routing for developers.",
            content_type="article",
            discovered_at=utcnow(),
            feed_at=utcnow(),
        )
    )
    connection.execute(
        tables["article_topics"]
        .insert()
        .values(
            article_id=article_id,
            topic_id=topic_id,
            role=role,
            origin=origin,
            relevance=0.4,
            evidence="Original topic evidence",
        )
    )
    return topic_id, article_id, category_id


@pytest.mark.parametrize("role", ["primary", "supporting", "comparison", "incidental"])
@pytest.mark.parametrize("origin", ["ai", "heuristic", "manual"])
def test_manual_category_membership_is_reconciled_and_survives_reanalysis(
    legacy_schema, role, origin
):
    connection, tables = legacy_schema
    topic_id, article_id, category_id = assignment_scenario(connection, tables, role, origin)
    connection.execute(
        tables["article_categories"]
        .insert()
        .values(
            article_id=article_id,
            category_id=category_id,
            origin="manual",
        )
    )
    revision = migration("0004_topics_ssot", connection)
    if origin == "manual" and role in {"comparison", "incidental"}:
        with connection.begin_nested() as transaction:
            with pytest.raises(RuntimeError, match="Reconcile the manual topic role"):
                revision.upgrade()
            transaction.rollback()
        assert connection.scalar(sa.text("SELECT count(*) FROM article_categories")) == 1
        assert connection.scalar(sa.text("SELECT role FROM article_topics")) == role
        return
    revision.upgrade()
    link = connection.execute(sa.text("SELECT * FROM article_topics")).one()
    assert link.role == ("primary" if role == "primary" else "supporting")
    assert link.origin == "manual"
    assert "Original topic evidence" in link.evidence
    original = connection.scalar(
        sa.text("SELECT record FROM taxonomy_migration_archive WHERE source_table='article_topics'")
    )
    assert original["role"] == role and original["origin"] == origin
    if origin == "manual":
        assert link.relevance == 0.4 and link.evidence == "Original topic evidence"
    else:
        assert "Migrated explicit category assignment" in link.evidence
    with Session(bind=connection) as session:
        article = session.get(Article, article_id)
        result = analysis.Classifications(
            developer_relevance="relevant",
            language="en",
            content_type="tutorial",
            content_format="article",
            topics=[],
            tags=[],
        )
        analysis.replace_classifications(session, article, result, origin="ai")
        session.flush()
        assert session.get(ArticleTopic, (article_id, topic_id)).origin == "manual"


def test_multiple_legacy_tags_merge_once_and_keep_manual_provenance(legacy_schema):
    connection, tables = legacy_schema
    _, article_id, category_id = assignment_scenario(connection, tables, "incidental", "ai")
    for i, origin in enumerate(["heuristic", "manual"]):
        tag_id = uuid.uuid4()
        connection.execute(
            tables["tags"]
            .insert()
            .values(
                id=tag_id,
                name=f"Tag {i}",
                slug=f"tag-{i}",
                aliases=[],
                category_id=category_id,
            )
        )
        connection.execute(
            tables["article_tags"]
            .insert()
            .values(
                article_id=article_id,
                tag_id=tag_id,
                origin=origin,
            )
        )
    migration("0004_topics_ssot", connection).upgrade()
    link = connection.execute(sa.text("SELECT * FROM article_topics")).one()
    assert link.role == "supporting" and link.origin == "manual"
    assert link.evidence.count("Migrated tag category membership") == 1
