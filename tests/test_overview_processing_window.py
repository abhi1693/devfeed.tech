"""Reporting windows must not discard old active work or count unrelated history."""

import uuid
from datetime import timedelta

import pytest
from devfeed_admin_api.overview_insights import processing
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from sqlalchemy import insert


@pytest.mark.integration
def test_processing_preserves_active_work_and_bounds_completed_window(database):
    now = utcnow()
    start = now - timedelta(days=7)
    article_id = uuid.uuid4()
    with database.begin() as session:
        session.add(
            Article(
                id=article_id,
                title="History",
                canonical_url="https://example.test/history",
                url_hash="a" * 64,
            )
        )
        session.flush()
        rows = [
            dict(
                article_id=article_id,
                status="succeeded",
                created_at=start - timedelta(days=2),
                finished_at=start - timedelta(days=1),
                duration_ms=9000,
            )
            for _ in range(2000)
        ]
        rows.extend(
            [
                dict(
                    article_id=article_id,
                    status="queued",
                    created_at=start - timedelta(days=10),
                    finished_at=None,
                    duration_ms=0,
                ),
                dict(
                    article_id=article_id,
                    status="succeeded",
                    created_at=start,
                    finished_at=start,
                    duration_ms=100,
                ),
                dict(
                    article_id=article_id,
                    status="failed",
                    created_at=now,
                    finished_at=now,
                    duration_ms=200,
                ),
                dict(
                    article_id=article_id,
                    status="succeeded",
                    created_at=now,
                    finished_at=now + timedelta(seconds=1),
                    duration_ms=300,
                ),
            ]
        )
        session.execute(insert(ArticleAnalysisJob), rows)
        result = next(item for item in processing(session, start, now) if item.kind == "analysis")
        assert result.queued == 1 and result.running == 0
        assert result.completed == result.failed == 1
        assert result.duration_ms == 300 and result.duration_runs == 2
        assert result.oldest_queued_at == start - timedelta(days=10)
