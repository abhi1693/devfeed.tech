"""Run real private workers with deterministic external specialist responses."""

from devfeed_aggregator import analysis_tasks
from devfeed_core.models import ArticleAnalysisJob, ArticleRefresh
from sqlalchemy import select

from tests.support.article_step_helpers import article_step_client


def complete_private_analysis(database, monkeypatch, article_id, *, before_complete=None):
    """Use the selected durable job, actual claim/checkpoints/apply/promotion commands."""
    with database() as session:
        owner = session.scalar(
            select(ArticleRefresh).where(
                ArticleRefresh.article_id == article_id,
                ArticleRefresh.status.in_(["active", "review"]),
            )
        )
        assert owner is not None
        job = session.get(ArticleAnalysisJob, owner.analysis_job_id)
        assert job is not None and job.refresh_id == owner.id
        identifier, refresh_id = job.id, owner.id
        topics = job.catalog_snapshot["topics"]
        assert topics
        text = job.input_snapshot["text"]
        title = job.input_snapshot["title"]
        topic_id = topics[0]["id"]
    calls = []

    def complete(prompt, schema):
        calls.append(schema)
        if before_complete is not None:
            before_complete(len(calls))
        return {
            "outcome": "ready",
            "developer_relevance": "relevant",
            "page_kind": "article",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "ai_title": title,
            "title_evidence": text,
            "ai_summary": "Inspect Python tasks with a complete private replacement.",
            "ai_description": "Diagnose Python task state with a reviewed replacement.",
            "topics": [
                {"topic_id": topic_id, "role": "primary", "relevance": 0.95, "evidence": text}
            ],
            "tags": [],
            "reasons": [],
        }

    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(identifier)
    return refresh_id, identifier, calls
