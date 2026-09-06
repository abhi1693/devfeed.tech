"""Publication transitions and durable analysis writes on disposable services."""

import pytest
from devfeed_core import analysis, editorial
from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, Source, Topic
from devfeed_core.urls import fingerprint
from sqlalchemy import select

pytestmark = pytest.mark.integration


def setup_article(database):
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://example.com/rss",
            source_type="publisher",
            approval_status="approved",
            enabled=False,
        )
        topic = Topic(name="Angular", slug="angular", kind="framework", status="active")
        article = Article(
            canonical_url="https://example.com/angular",
            url_hash=fingerprint("https://example.com/angular"),
            title="Angular routing",
            summary=(
                "Angular routing helps developers build navigation "
                "and organize their JavaScript applications."
            ),
            language="en",
        )
        session.add_all([source, topic, article])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="angular",
                original_url=article.canonical_url,
            )
        )
        session.flush()
        return article.id, topic.id


def test_analysis_then_explicit_publication_and_cached_unpublish(database, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    close_cache()
    try:
        article_id, topic_id = setup_article(database)
        path = f"/v1/articles/{article_id}"
        assert client.get(path).status_code == 404
        with database.begin() as session:
            job = analysis.request_analysis(session, article_id)
            job.model = "test-model"
            original = session.get(Article, article_id).summary
            result = analysis.AnalysisResult(
                outcome="ready",
                developer_relevance="relevant",
                language="en",
                content_type="tutorial",
                content_format="article",
                ai_summary="AI generated explanation of Angular routing.",
                ai_description=None,
                topics=[
                    analysis.TopicSelection(
                        topic_id=topic_id,
                        role="primary",
                        relevance=0.95,
                        evidence="Angular routing",
                    )
                ],
                categories=[],
                tags=[],
                proposed_topics=[],
                reasons=[],
            )
            analysis.validate_evidence(result, job.input_snapshot, analysis.catalog(session))
            analysis.apply_analysis(session, session.get(Article, article_id), job, result)
            assert job.outcome == "applied"
        assert client.get(path).status_code == 404
        with database.begin() as session:
            editorial.decide_article(
                session, article_id, editorial.EditorialDecision(action="approve")
            )
        assert client.get(path).status_code == 404
        with database.begin() as session:
            editorial.decide_article(
                session, article_id, editorial.EditorialDecision(action="publish")
            )
        payload = client.get(path).json()
        assert payload["summary"] == original and payload["ai_summary"] != original
        assert client.get(path).headers["x-cache"] == "HIT"
        assert client.get("/v1/feed?topic=angular").json()["items"][0]["id"] == str(article_id)
        with database.begin() as session:
            editorial.decide_article(
                session, article_id, editorial.EditorialDecision(action="unpublish")
            )
        assert client.get(path).status_code == 404
        assert client.get("/v1/feed?topic=angular").json()["items"] == []
    finally:
        close_cache()


def test_content_changes_and_manual_decisions_discard_inflight_result(database):
    article_id, _ = setup_article(database)
    with database.begin() as session:
        job = analysis.request_analysis(session, article_id)
        job_id = job.id
    with database.begin() as session:
        editorial.decide_article(
            session,
            article_id,
            editorial.EditorialDecision(action="reject", note="Operator decision"),
        )
    with database.begin() as session:
        article = session.get(Article, article_id)
        job = session.get(ArticleAnalysisJob, job_id)
        result = analysis.AnalysisResult(
            outcome="insufficient_evidence",
            developer_relevance="uncertain",
            language=None,
            content_type=None,
            content_format=None,
            ai_summary=None,
            ai_description=None,
            topics=[],
            categories=[],
            tags=[],
            proposed_topics=[],
            reasons=[],
        )
        analysis.apply_analysis(session, article, job, result)
        assert job.outcome == "superseded"
        assert article.review_status == "rejected" and article.publication_status == "unpublished"
    with database() as session:
        assert (
            session.scalar(
                select(ArticleAnalysisJob.outcome).where(ArticleAnalysisJob.id == job_id)
            )
            == "superseded"
        )
