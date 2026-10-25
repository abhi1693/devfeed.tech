"""The index-only lifecycle migration can roll forward/back without data changes."""

import uuid

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.models import Article
from sqlalchemy import text

from tests.support.paths import REPO_ROOT


def parent_indexes(session):
    return dict(
        session.execute(
            text(
                "SELECT c.relname,c.oid FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid "
                "WHERE c.relnamespace=current_schema()::regnamespace AND i.indisvalid "
                "AND c.relname IN ('ix_article_analysis_latest','ix_article_enrichment_latest')"
            )
        ).all()
    )


@pytest.mark.integration
def test_lifecycle_indexes_upgrade_downgrade_and_preserve_articles(database):
    config = Config(str(REPO_ROOT / "alembic.ini"))
    with database.begin() as session:
        article = Article(
            canonical_url="https://publisher.example/lifecycle-migration",
            url_hash="lifecycle-migration",
            title="Existing published article",
            summary="Retained source summary",
            review_status="approved",
            publication_status="published",
        )
        session.add(article)
        session.flush()
        identifier = article.id
        retained = parent_indexes(session)
        assert len(retained) == 2
    try:
        command.downgrade(config, "0032")
        with database() as session:
            assert (
                session.scalar(
                    text("SELECT publication_status FROM articles WHERE id=:id"), {"id": identifier}
                )
                == "published"
            )
            assert parent_indexes(session) == retained
            assert session.scalar(text("SELECT to_regclass('ix_article_image_history')")) is None
            predicate = session.scalar(
                text(
                    "SELECT pg_get_expr(indpred,indrelid) FROM pg_index "
                    "WHERE indexrelid='ix_articles_automation_due'::regclass"
                )
            )
            assert "pending" in predicate and "approved" not in predicate
        command.upgrade(config, "head")
        with database() as session:
            before = session.get(Article, identifier)
            assert (
                before.title == "Existing published article"
                and before.publication_status == "published"
            )
            assert parent_indexes(session) == retained
            rows = session.execute(
                text(
                    "SELECT c.relname,i.indisvalid FROM pg_index i "
                    "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname IN "
                    "('ix_article_analysis_latest','ix_article_enrichment_latest',"
                    "'ix_article_image_history','ix_articles_automation_due')"
                )
            ).all()
            assert len(rows) == 4 and all(valid for _, valid in rows)
            predicate = session.scalar(
                text(
                    "SELECT pg_get_expr(indpred,indrelid) FROM pg_index "
                    "WHERE indexrelid='ix_articles_automation_due'::regclass"
                )
            )
            assert "approved" in predicate and "pending" in predicate
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM pg_class WHERE relname IN "
                        "('ix_articles_automation_due_next','ix_articles_automation_due_previous',"
                        "'ix_article_analysis_history','ix_article_enrichment_history')"
                    )
                )
                == 0
            )
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_automation_migration_marks_existing_debt_without_changing_new_insert_defaults(database):
    config = Config(str(REPO_ROOT / "alembic.ini"))
    with database.begin() as session:
        article = Article(
            canonical_url="https://publisher.example/preexisting-automation",
            url_hash="preexisting-automation",
            title="Historical pending article",
            summary="Evidence",
        )
        session.add(article)
        session.flush()
        old_id = article.id
    new_id = uuid.uuid4()
    try:
        command.downgrade(config, "0039")
        command.upgrade(config, "0040")
        with database.begin() as session:
            assert (
                session.scalar(
                    text("SELECT automation_wait_reason FROM articles WHERE id=:id"), {"id": old_id}
                )
                == "maintenance"
            )
            session.execute(
                text(
                    "INSERT INTO articles "
                    "(id,canonical_url,url_hash,title,summary,review_status,publication_status,"
                    "content_type,content_format,feed_at,discovered_at) "
                    "VALUES (:id,:url,:hash,'New foreground article','Evidence','pending',"
                    "'unpublished','article','article',now(),now())"
                ),
                {"id": new_id, "url": f"https://publisher.example/{new_id}", "hash": new_id.hex},
            )
            assert (
                session.scalar(
                    text("SELECT automation_wait_reason FROM articles WHERE id=:id"), {"id": new_id}
                )
                is None
            )
        command.upgrade(config, "head")
        with database() as session:
            rows = session.execute(
                text(
                    "SELECT relname,indisvalid FROM pg_index JOIN pg_class ON oid=indexrelid "
                    "WHERE relname IN ('ix_articles_automation_lane_due',"
                    "'ix_articles_automation_identity','ix_article_origins_source_article')"
                )
            ).all()
            assert len(rows) == 3 and all(valid for _, valid in rows)
    finally:
        command.upgrade(config, "head")
