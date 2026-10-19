"""The index-only lifecycle migration can roll forward/back without data changes."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.models import Article
from sqlalchemy import text


@pytest.mark.integration
def test_lifecycle_indexes_upgrade_downgrade_and_preserve_articles(database):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
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
    command.downgrade(config, "0026")
    try:
        with database() as session:
            assert session.get(Article, identifier).publication_status == "published"
            assert session.scalar(text("SELECT to_regclass('ix_article_analysis_history')")) is None
        command.upgrade(config, "head")
        with database() as session:
            before = session.get(Article, identifier)
            assert (
                before.title == "Existing published article"
                and before.publication_status == "published"
            )
            rows = session.execute(
                text(
                    "SELECT c.relname,i.indisvalid FROM pg_index i "
                    "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname IN "
                    "('ix_article_analysis_history','ix_article_enrichment_history',"
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
                        "('ix_articles_automation_due_next','ix_articles_automation_due_previous')"
                    )
                )
                == 0
            )
    finally:
        command.upgrade(config, "head")
