"""Shared migration DDL preserves transactional visibility invalidation semantics."""

import importlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, ArticleTopic, Source, Tag, Topic
from sqlalchemy import insert, text

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "revision", ["0028_search_tag_visibility_revision", "0029_search_topic_visibility_revision"]
)
def test_visibility_migration_round_trip_restores_exact_functions_and_triggers(database, revision):
    migration = importlib.import_module(f"migrations.versions.{revision}")
    with database() as session, session.get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            definitions = {}
            for table in migration.TABLES:
                definitions[table] = connection.execute(
                    text("""SELECT pg_get_functiondef(p.oid), pg_get_triggerdef(t.oid)
                    FROM pg_proc p JOIN pg_trigger t ON t.tgfoid=p.oid
                    WHERE p.oid=to_regprocedure(:function) ORDER BY t.tgname"""),
                    {"function": f"refresh_search_tags_{table}()"},
                ).all()
                assert len(definitions[table]) == 4
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                for table in migration.TABLES:
                    assert (
                        connection.scalar(
                            text("SELECT to_regprocedure(:function)"),
                            {"function": f"refresh_search_tags_{table}()"},
                        )
                        is None
                    )
                migration.upgrade()
            for table in migration.TABLES:
                restored = connection.execute(
                    text("""SELECT pg_get_functiondef(p.oid), pg_get_triggerdef(t.oid)
                    FROM pg_proc p JOIN pg_trigger t ON t.tgfoid=p.oid
                    WHERE p.oid=to_regprocedure(:function) ORDER BY t.tgname"""),
                    {"function": f"refresh_search_tags_{table}()"},
                ).all()
                assert restored == definitions[table]
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    "model,unrelated,changed",
    [
        (Article, "title='Renamed article'", "publication_status='unpublished'"),
        (Source, "name='Renamed publisher'", "approval_status='rejected'"),
        (ArticleOrigin, "original_url='https://other.test/article'", "source_id=:other_source"),
        (ArticleTag, "origin='manual'", "tag_id=:other_tag"),
        (ArticleTopic, "evidence='Updated supporting passage'", "role='incidental'"),
    ],
)
def test_each_visibility_table_ignores_empty_or_irrelevant_writes_but_tracks_real_changes(
    database, model, unrelated, changed
):
    with database.begin() as session:
        source, other_source = [
            Source(
                name=f"Publisher {index}",
                feed_url=f"https://publisher{index}.test/feed",
                source_type="publisher",
                approval_status="approved",
            )
            for index in range(2)
        ]
        article = Article(
            title="Visible article",
            canonical_url="https://publisher.test/article",
            url_hash="a" * 64,
            publication_status="published",
            review_status="approved",
        )
        tag, other_tag = [Tag(name=f"Tag {index}", slug=f"tag-{index}") for index in range(2)]
        topic = Topic(name="Topic", slug="topic", kind="technology", status="active")
        session.add_all([source, other_source, article, tag, other_tag, topic])
        session.flush()
        values = {
            Article: {
                "id": article.id,
                "title": article.title,
                "canonical_url": article.canonical_url,
                "url_hash": article.url_hash,
                "publication_status": "published",
                "review_status": "approved",
            },
            Source: {
                "id": source.id,
                "name": source.name,
                "feed_url": source.feed_url,
                "source_type": "publisher",
                "approval_status": "approved",
            },
            ArticleOrigin: {
                "id": uuid.uuid4(),
                "article_id": article.id,
                "source_id": source.id,
                "entry_key": "article",
                "original_url": article.canonical_url,
            },
            ArticleTag: {"article_id": article.id, "tag_id": tag.id},
            ArticleTopic: {
                "article_id": article.id,
                "topic_id": topic.id,
                "role": "primary",
                "relevance": 1,
                "evidence": "Source article",
            },
        }
        for link in (ArticleOrigin, ArticleTag, ArticleTopic):
            session.execute(insert(link.__table__), values[link])
        table = model.__table__.name
        column = "id" if model in {Article, Source, ArticleOrigin} else "article_id"
        parameters = {
            "id": values[model][column],
            "other_source": other_source.id,
            "other_tag": other_tag.id,
        }

        def revision():
            return session.scalar(
                text("SELECT revision FROM catalog_revisions WHERE name='search_tag_visibility'")
            )

        before = revision()
        for statement in (
            f"INSERT INTO {table}({column}) SELECT {column} FROM {table} WHERE false",
            f"UPDATE {table} SET {column}={column} WHERE false",
            f"DELETE FROM {table} WHERE false",
            f"UPDATE {table} SET {column}={column} WHERE {column}=:id",
            f"UPDATE {table} SET {unrelated} WHERE {column}=:id",
        ):
            session.execute(text(statement), parameters)
            assert revision() == before
        session.execute(text(f"UPDATE {table} SET {changed} WHERE {column}=:id"), parameters)
        assert revision() != before
        if model is Source:
            session.execute(
                text("UPDATE article_origins SET source_id=:other_source WHERE source_id=:id"),
                parameters,
            )
        before = revision()
        session.execute(text(f"DELETE FROM {table} WHERE {column}=:id"), parameters)
        assert revision() != before
        before = revision()
        session.execute(insert(model.__table__), values[model])
        assert revision() != before
        before = revision()
        session.execute(text(f"TRUNCATE {table} CASCADE"))
        assert revision() != before
