"""Saved publication reports use narrow review identities with exact intervention cutoffs."""

from datetime import timedelta

import pytest
from devfeed_admin_api.automation import _publication_automation_query, publication_automation
from devfeed_core.models import ArticleReview, utcnow
from sqlalchemy import text
from test_pipeline_projection_safety import add_article

pytestmark = pytest.mark.integration


def test_large_review_evidence_uses_partial_indexes_without_changing_manual_cutoff(database):
    now = utcnow()
    evidence = {"policy": "automatic", "evidence": "Unicode extraction evidence 設計 " * 3000}
    with database.begin() as session:
        for index in range(2):
            article = add_article(session, published=True, published_at=now)
            article.discovered_at = now - timedelta(hours=1)
            session.add_all(
                [
                    ArticleReview(
                        article_id=article.id,
                        action="publish",
                        revision=1,
                        automation=evidence,
                        created_at=now,
                    ),
                    ArticleReview(
                        article_id=article.id,
                        action="approve",
                        revision=2,
                        automation={},
                        created_at=now + timedelta(microseconds=1) if index == 0 else now,
                    ),
                ]
            )
    with database() as session:
        result = publication_automation(session, now - timedelta(days=1), now)
        assert result == {
            "published_in_window": 2,
            "published_without_intervention": 1,
            "automatic_publication_percent": 50.0,
            "median_ingestion_to_publication_seconds": 3600.0,
        }
        # Small fixtures normally favor a table scan. This verifies that both
        # exact application predicates can use narrow indexes at large scale.
        session.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = _publication_automation_query(now - timedelta(days=1), now).compile(
            session.get_bind()
        )
        plan = "\n".join(
            row[0]
            for row in session.connection().exec_driver_sql(
                "EXPLAIN " + str(compiled), compiled.params
            )
        )
    assert "ix_article_reviews_manual_publication" in plan
    assert "ix_article_reviews_automatic_publication" in plan
    assert "Seq Scan on article_reviews" not in plan
