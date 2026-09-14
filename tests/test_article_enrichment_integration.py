"""Application behavior only; requires explicitly configured disposable test services."""

from concurrent.futures import ThreadPoolExecutor

import httpcore
import pytest
from devfeed_aggregator import article_tasks, tasks
from devfeed_core import cache
from devfeed_core.article_jobs import backfill_articles, request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    Source,
)
from sqlalchemy import func, select
from test_fetcher import use_pool

pytestmark = pytest.mark.integration

PAGE = b"""<html><head><title>Original publisher title</title>
<meta name="author" content="Original Writer">
<meta property="og:image" content="/cover.jpg">
<meta property="article:published_time" content="2026-09-04T07:00:00Z"></head>
<body><article><h1>Original publisher title</h1><p>
This article explains how developers can build reliable applications with Python and store
their information in a database. We discuss common problems and test all changes before
making the application available to readers. These examples explain how the system works.
</p><p>Readers can follow along with the examples to learn about the different options.
The final section describes how to handle failures and recover without losing information.
</p></article></body></html>"""


@pytest.fixture
def discovery(database, rss_bytes, monkeypatch):
    with database.begin() as session:
        source = Source(
            name="Community",
            feed_url="https://example.com/rss",
            source_type="aggregator",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        source_id, job_id = source.id, request_ingestion(session, source).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(str(job_id))
    with database() as session:
        article_id = session.scalar(select(Article.id).where(Article.title.like("%Python%")))
        enrichment_id = session.scalar(
            select(ArticleEnrichmentJob.id).where(ArticleEnrichmentJob.article_id == article_id)
        )
    return source_id, job_id, article_id, enrichment_id


def test_ingestion_enqueues_page_not_duplicate_image_jobs_and_backfill_skips_attempted(
    database, discovery
):
    with database.begin() as session:
        assert session.scalar(select(func.count()).select_from(ArticleEnrichmentJob)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 0
        assert backfill_articles(session, 100) == []


def test_page_metadata_and_job_outcomes_reach_cached_api_without_changing_identity(
    database, client, discovery, monkeypatch, publish_for_read_test, admin_client
):
    _, _, article_id, job_id = discovery
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    monkeypatch.setattr(
        article_tasks, "fetch_article_page", lambda url: FetchResult(200, PAGE, url)
    )
    path = f"/v1/articles/{article_id}"
    try:
        assert client.get(path).status_code == 404
        publish_for_read_test([article_id])
        before = client.get(path).json()
        assert before["summary"] == "" and client.get(path).headers["x-cache"] == "HIT"
        article_tasks.enrich_article(str(job_id))
        assert client.get(path).status_code == 404  # Changed content requires approval again.
        publish_for_read_test([article_id])
        response = client.get(path)
        after = response.json()
        assert response.headers["x-cache"] == "MISS"
        assert after["title"] == "Original publisher title" and after["author"] == "Original Writer"
        assert after["language"] == "en" and after["metadata_source_type"] == "page"
        assert 0 < len(after["summary"]) <= 500 and after["image_url"]
        for key in ["id", "canonical_url", "feed_at", "discovered_at", "origins", "sources"]:
            assert after[key] == before[key]
        assert after["origins"][0]["source_metadata"]["submitter"] == "Example Author"
        status = admin_client.get(f"/v1/admin/ingestion/article-jobs/{job_id}").json()
        assert status["status"] == "succeeded" and status["outcome"] == "enriched"
        assert "summary" in status["changed_fields"]
        assert (
            len(
                admin_client.get(f"/v1/admin/ingestion/article-jobs?article_id={article_id}").json()
            )
            == 1
        )
        article_tasks.enrich_article(str(job_id))
        assert client.get(path).headers["x-cache"] == "HIT"  # Duplicate delivery is a no-op.
    finally:
        cache.close_cache()


def test_concurrent_requests_reuse_one_active_article_job(database, discovery):
    _, _, article_id, job_id = discovery

    def request(_):
        with database.begin() as session:
            return request_article_enrichment(session, article_id).id

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert set(pool.map(request, range(3))) == {job_id}


def test_large_article_enriches_complete_body_without_saving_scripts(
    database, discovery, monkeypatch
):
    _, _, article_id, job_id = discovery
    # Modern sites can exceed the metadata budget before their article even starts.
    # Keep the meaningful content after 3 MB of hydration data to detect truncation.
    script = b'<script>window.hydration="' + b"x" * 3_000_000 + b'";</script>'
    body = PAGE.replace(b"<body>", b"<body>" + script)
    assert len(body) > get_settings().page_max_bytes
    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-type": "text/html"}, content=body)],
    )
    article_tasks.enrich_article(str(job_id))
    with database() as session:
        job = session.get(ArticleEnrichmentJob, job_id)
        content = session.get(ArticleContent, article_id)
        article = session.get(Article, article_id)
        assert job.status == "succeeded" and job.outcome == "enriched"
        assert job.http_status == 200 and job.attempts == 1
        assert article.title == "Original publisher title" and article.author == "Original Writer"
        assert article.image_url == "https://example.com/cover.jpg"
        assert article.publication_status == "unpublished"
        assert "recover without losing information" in content.text
        assert "hydration" not in content.text and "xxxxx" not in content.text
        assert 100 < len(content.text) <= 60_000


@pytest.mark.parametrize("change", ["publisher", "rejection"])
def test_page_cannot_overwrite_publisher_or_rejected_source_during_fetch(
    database, discovery, monkeypatch, change
):
    source_id, _, article_id, job_id = discovery

    def fetch(url):
        with database.begin() as session:
            if change == "publisher":
                article = session.get(Article, article_id)
                article.metadata_source_type = "publisher"
                article.title, article.summary = "Publisher wins", "Authoritative RSS excerpt"
            else:
                session.get(Source, source_id).approval_status = "rejected"
        return FetchResult(200, PAGE, url)

    monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
    article_tasks.enrich_article(str(job_id))
    with database() as session:
        article, job = session.get(Article, article_id), session.get(ArticleEnrichmentJob, job_id)
        assert article.title != "Original publisher title"
        assert job.outcome == ("enriched" if change == "publisher" else "unapproved")
        if change == "publisher":
            assert article.summary == "Authoritative RSS excerpt"


def test_failed_ingestion_rolls_back_page_jobs_with_articles(database, monkeypatch, rss_bytes):
    with database.begin() as session:
        source = Source(
            name="Community",
            feed_url="https://example.com/rss",
            source_type="aggregator",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        job_id = request_ingestion(session, source).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    store = tasks.store_entries

    def fail(*args):
        store(*args)
        raise RuntimeError("simulated failure after outbox creation")

    monkeypatch.setattr(tasks, "store_entries", fail)
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        assert session.scalar(select(func.count()).select_from(ArticleEnrichmentJob)) == 0


@pytest.mark.parametrize(
    "status,variant,rejected",
    [
        (404, "empty", True),
        (410, "empty", True),
        (503, "empty", False),
        (429, "empty", False),
        (403, "empty", False),
        (404, "summary", False),
        (404, "content", False),
        (404, "approved", False),
        (404, "published", False),
        (404, "changed_url", False),
        (404, "automation_disabled", False),
        (404, "rejected", False),
    ],
)
def test_missing_publisher_article_rejection_is_audited_and_preserves_valid_content(
    database, discovery, monkeypatch, status, variant, rejected
):
    from devfeed_core.feeds.fetcher import FeedError
    from devfeed_core.models import ArticleReview

    _, _, article_id, job_id = discovery
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", variant != "automation_disabled")
    # The test isolates extraction policy; no model is contacted.
    requested = []
    monkeypatch.setattr(article_tasks, "request_analysis", lambda *a, **k: requested.append(a[1]))
    with database.begin() as session:
        article = session.get(Article, article_id)
        article.summary = "Publisher-provided evidence" if variant == "summary" else ""
        article.review_status = variant if variant in {"approved", "rejected"} else "pending"
        article.publication_status = "published" if variant == "published" else "unpublished"
        if variant == "published":
            article.review_status = "approved"
        content = session.get(ArticleContent, article_id)
        if content is not None:
            session.delete(content)
        if variant == "content":
            session.add(
                ArticleContent(
                    article_id=article_id,
                    text="Original publisher article",
                    content_hash="a" * 64,
                    url=article.canonical_url,
                    method="page",
                )
            )
        revision = article.editorial_revision

    def fetch(url):
        if variant == "changed_url":
            with database.begin() as session:
                session.get(Article, article_id).canonical_url = url + "-corrected"
        raise FeedError(
            "Private upstream response",
            reason="http_error",
            status=status,
            retryable=status in {429, 503},
        )

    monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
    article_tasks.enrich_article(str(job_id))
    with database() as session:
        article = session.get(Article, article_id)
        reviews = session.scalars(
            select(ArticleReview).where(ArticleReview.article_id == article_id)
        ).all()
        assert (article.review_status == "rejected") == (rejected or variant == "rejected")
        assert len(reviews) == int(rejected)
        if rejected:
            assert not requested
            assert article.editorial_revision == revision + 1
            assert reviews[0].action == "reject"
            assert reviews[0].automation == {
                "policy": "missing-publisher-article-v1",
                "job_id": str(job_id),
                "http_status": status,
            }
            assert str(status) in reviews[0].note
            assert "Private upstream response" not in reviews[0].note
        if variant == "changed_url":
            assert not requested
        job = session.get(ArticleEnrichmentJob, job_id)
        assert job.http_status == status
        assert job.status == ("queued" if status in {429, 503} else "failed")
