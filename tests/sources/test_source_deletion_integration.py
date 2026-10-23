"""Source deletion retains article records and invalidates in-flight ingestion."""

from unittest.mock import Mock

import pytest
from devfeed_aggregator import tasks
from devfeed_core import cache_events
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOrigin,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    SourcePublicationPolicyReview,
    SourceReview,
    UserAccount,
    UserSource,
)
from devfeed_core.urls import fingerprint
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed"])
def test_delete_source_keeps_articles_and_other_origins(
    database, admin_client, client, monkeypatch, status
):
    with database.begin() as session:
        source = Source(
            name="Deleted",
            feed_url="https://deleted.example/feed",
            approval_status="approved",
            source_type="publisher",
        )
        other = Source(
            name="Kept",
            feed_url="https://kept.example/feed",
            approval_status="approved",
            source_type="publisher",
        )
        user = UserAccount(issuer="test", subject="reader", organization_id="test")
        articles = [
            Article(
                title=name,
                canonical_url=f"https://example.com/{name}",
                url_hash=fingerprint(f"https://example.com/{name}"),
                summary="Preserved article content",
                review_status="approved",
                publication_status="published",
            )
            for name in ("exclusive", "shared")
        ]
        session.add_all([source, other, user, *articles])
        session.flush()
        source_id, other_id, user_id = source.id, other.id, user.id
        article_ids = [article.id for article in articles]
        for article in articles:
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key=str(article.id),
                    original_url=article.canonical_url,
                )
            )
            session.add(ArticleLike(article_id=article.id, user_id=user.id))
        session.add_all(
            [
                ArticleOrigin(
                    article_id=articles[1].id,
                    source_id=other.id,
                    entry_key="shared",
                    original_url=articles[1].canonical_url,
                ),
                UserSource(user_id=user.id, source_id=source.id),
                UserSource(user_id=user.id, source_id=other.id),
                SourceReview(source_id=source.id, decision="approved"),
                SourcePublicationPolicyReview(
                    source_id=source.id, mode="auto", revision=1, actor="test"
                ),
                IngestionJob(source_id=source.id, status=status),
                SourceEnrichmentJob(source_id=source.id, status=status),
                IngestionJob(source_id=other.id),
            ]
        )
    assert len(client.get("/v1/feed").json()["items"]) == 2
    invalidate = Mock()
    monkeypatch.setattr(cache_events, "invalidate_public_cache", invalidate)
    response = admin_client.delete(f"/v1/admin/sources/{source_id}")
    assert response.status_code == 204, response.text
    invalidate.assert_called_once()
    with database() as session:
        assert session.get(Source, source_id) is None
        assert session.get(Source, other_id) is not None
        for model in (
            ArticleOrigin,
            IngestionJob,
            SourceEnrichmentJob,
            SourceReview,
            SourcePublicationPolicyReview,
            UserSource,
        ):
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.source_id == source_id)
                )
                == 0
            )
        for article_id in article_ids:
            assert session.get(Article, article_id).summary == "Preserved article content"
            assert session.get(ArticleLike, (article_id, user_id)) is not None
        assert session.get(UserSource, (user_id, other_id)) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(IngestionJob.source_id == other_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ArticleOrigin)
                .where(ArticleOrigin.source_id == other_id)
            )
            == 1
        )
    assert [item["id"] for item in client.get("/v1/feed").json()["items"]] == [str(article_ids[1])]
    assert admin_client.get(f"/v1/admin/articles/{article_ids[0]}").status_code == 200
    assert admin_client.delete(f"/v1/admin/sources/{source_id}").status_code == 404


@pytest.mark.parametrize("result", ["queued", "success", "failure"])
def test_deleted_source_discards_ingestion_deliveries_and_late_results(
    database, admin_client, monkeypatch, rss_bytes, result
):
    with database.begin() as session:
        source = Source(
            name="Example",
            feed_url="https://example.com/rss",
            approval_status="approved",
            source_type="publisher",
        )
        session.add(source)
        session.flush()
        source_id, job_id = source.id, request_ingestion(session, source).id

    def delete_source():
        response = admin_client.delete(f"/v1/admin/sources/{source_id}")
        assert response.status_code == 204, response.text

    def fetch(url, *args):
        assert result != "queued", "Deleted jobs must not fetch feeds"
        delete_source()
        if result == "failure":
            raise FeedError("HTTP 503", status=503, retryable=True)
        return FetchResult(200, rss_bytes, url)

    monkeypatch.setattr(tasks, "fetch_feed", fetch)
    if result == "queued":
        delete_source()
    tasks.ingest(str(job_id))
    tasks.ingest(str(job_id))  # Redelivery must remain a no-op.
    with database() as session:
        assert session.get(Source, source_id) is None
        assert session.get(IngestionJob, job_id) is None
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 0
