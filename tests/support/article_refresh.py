"""Approved public articles and guarded maintenance requests for lifecycle tests."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from devfeed_core.article_processing_retry import RetryTarget, request_processing_retry
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    FeedNotificationEvent,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.admin.articles.test_article_pipeline import make_article

JOB_MODELS = {
    "enrichment": ArticleEnrichmentJob,
    "analysis": ArticleAnalysisJob,
    "images": ArticleImageJob,
}
JOB_SLOTS = {
    "enrichment": "enrichment_job_id",
    "analysis": "analysis_job_id",
    "images": "discovery_job_id",
}


@dataclass(frozen=True)
class PublishedRetryCase:
    article_id: uuid.UUID
    slug: str
    published_at: datetime
    body: dict[str, Any]

    @property
    def path(self) -> str:
        return f"/v1/articles/{self.slug}"

    def request(self, session: Session, target: RetryTarget) -> Article:
        article = session.get(Article, self.article_id)
        assert article is not None
        model = JOB_MODELS[target]
        previous = session.scalar(
            select(model)
            .where(model.article_id == self.article_id, model.refresh_id.is_(None))
            .order_by(model.created_at.desc(), model.id.desc())
            .limit(1)
        )
        return request_processing_retry(
            session,
            self.article_id,
            target=target,
            expected_revision=article.editorial_revision,
            expected_job_id=previous.id if previous else None,
            actor={"subject": "maintenance-public-test"},
        )


@pytest.fixture
def published_retry_case(database, client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    monkeypatch.setattr(settings, "published_article_refresh_enabled", True)
    with database.begin() as session:
        article = make_article(session, state="published")
        article.content_type, article.content_format = "tutorial", "article"
        session.add(
            FeedNotificationEvent(
                article_id=article.id,
                topic_ids=[link.topic_id for link in article.topic_links],
                source_ids=[origin.source_id for origin in article.origins],
                created_at=article.published_to_feed_at,
            )
        )
        session.flush()
        identifier, slug, published = article.id, article.slug, article.published_to_feed_at
    before = client.get(f"/v1/articles/{slug}")
    assert before.status_code == 200
    return PublishedRetryCase(identifier, slug, published, before.json())


def complete_private_enrichment(database, monkeypatch, article_id):
    """Run extraction against a local publisher response, including real child admission."""
    from devfeed_aggregator import article_tasks
    from devfeed_core.feeds.fetcher import FetchResult
    from devfeed_core.models import ArticleRefresh

    from tests.articles import test_article_enrichment_integration as publisher_fixture

    with database() as session:
        owner = session.scalar(
            select(ArticleRefresh).where(
                ArticleRefresh.article_id == article_id,
                ArticleRefresh.status.in_(["active", "review"]),
            )
        )
        assert owner is not None and owner.enrichment_job_id is not None
        job_id = owner.enrichment_job_id
    monkeypatch.setattr(article_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        article_tasks,
        "fetch_article_page",
        lambda url: FetchResult(
            200,
            publisher_fixture.PAGE.replace(b'<meta property="og:image" content="/cover.jpg">', b""),
            url,
        ),
    )
    article_tasks.enrich_article(str(job_id))
