import uuid
from datetime import UTC, datetime

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_core import analysis
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleAnalysisJob, TopicProposal
from devfeed_core.services import OperationConflict
from devfeed_core.topic_analysis import request_topic_analysis
from test_ai_content_cutoff import set_cutoff
from test_editorial_integration import setup_article

pytestmark = pytest.mark.integration


def test_cutoff_blocks_new_work_and_resume_creates_a_fresh_durable_job(database, monkeypatch):
    article_id, _ = setup_article(database)
    with database.begin() as session:
        article = session.get(Article, article_id)
        article.published_at = datetime(2026, 8, 1, tzinfo=UTC)
        original = analysis.request_analysis(session, article_id)
        original_id = original.id
    set_cutoff(monkeypatch)
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: pytest.fail("Started inference"))
    analysis_tasks._analyze(original_id)
    with database.begin() as session:
        original = session.get(ArticleAnalysisJob, original_id)
        assert original.outcome == "content_date_deferred" and original.attempts == 0
        assert analysis.request_analysis(session, article_id, automatic=True, force=True) is None
        with pytest.raises(OperationConflict, match="publication date"):
            analysis.request_analysis(session, article_id, force=True)
        assert analysis.backfill_analyses(session, 10, force=True)[0] == []
    set_cutoff(monkeypatch, "2026-08-01")
    with database.begin() as session:
        resumed = analysis.request_analysis(session, article_id, automatic=True)
        assert resumed is not None and resumed.id != original_id
        assert resumed.status == "queued" and resumed.attempts == 0
        assert session.get(ArticleAnalysisJob, original_id).outcome == "content_date_deferred"
        assert session.get(Article, article_id).review_status == "pending"


def test_backfill_uses_source_date_and_topic_research_remains_available(database, monkeypatch):
    article_id, _ = setup_article(database)
    set_cutoff(monkeypatch)
    with database.begin() as session:
        article = session.get(Article, article_id)
        article.published_at = datetime(2026, 9, 1, tzinfo=UTC)
        article.discovered_at = datetime(2025, 1, 1, tzinfo=UTC)
        session.flush()
        jobs, _, _ = analysis.backfill_analyses(session, 10)
        assert [job.article_id for job in jobs] == [article_id]
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="undated-research",
            action="create",
            origin="import",
            source_name="Test",
            proposed={"name": "Research", "slug": "undated-research"},
            created_by={},
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        session.add(proposal)
        session.flush()
        assert request_topic_analysis(session, proposal.id, {}).status == "queued"
