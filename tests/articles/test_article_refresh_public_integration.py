"""Approved public projections survive private retries until an authorized swap."""

import pytest
from devfeed_core import cache, editorial, sitemaps
from devfeed_core.article_processing_retry import request_processing_retry
from devfeed_core.article_refresh import touch_refresh, try_promote_refresh
from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleImageJob,
    ArticleRefresh,
    FeedNotificationEvent,
    utcnow,
)
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import review_source
from sqlalchemy import func, select

from tests.admin.articles.test_article_pipeline import make_article
from tests.support import article_maintenance_workers as workers_support
from tests.support import article_refresh as support
from tests.support.article_refresh import published_retry_case as published_retry_case

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cached_public_reads(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    yield
    cache.close_cache()


def assert_approved_publication(client, database, case):
    response = client.get(case.path)
    assert response.status_code == 200 and response.json() == case.body
    assert response.headers["x-cache"] == "HIT"
    feed = client.get("/v1/feed?languages=en").json()
    assert str(case.article_id) in {item["id"] for item in feed["items"]}
    with database() as session:
        article = session.get(Article, case.article_id)
        assert article.slug == case.slug and article.published_to_feed_at == case.published_at
        assert article.publication_status == "published" and article.review_status == "approved"
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 1


@pytest.mark.parametrize("target", ["enrichment", "analysis", "images"])
def test_pending_and_failed_private_maintenance_keep_cached_detail_and_feed(
    database, client, published_retry_case, target
):
    case = published_retry_case
    assert_approved_publication(client, database, case)
    with database.begin() as session:
        case.request(session, target)
        owner = session.scalar(
            select(ArticleRefresh).where(ArticleRefresh.article_id == case.article_id)
        )
        assert owner.status == "active"
        refresh_id = owner.id
        job_id = getattr(owner, support.JOB_SLOTS[target])
        job = session.get(support.JOB_MODELS[target], job_id)
        assert job.refresh_id == owner.id and job.status == "queued"
    assert_approved_publication(client, database, case)
    with database.begin() as session:
        job = session.get(support.JOB_MODELS[target], job_id)
        job.status, job.error, job.finished_at = (
            "failed",
            "Controlled maintenance failure",
            utcnow(),
        )
    assert try_promote_refresh(database, refresh_id) != "promoted"
    assert_approved_publication(client, database, case)


@pytest.mark.parametrize("withdrawal", ["unpublish", "reject", "source"])
def test_explicit_withdrawal_invalidates_cached_public_access_during_maintenance(
    database, client, published_retry_case, withdrawal
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "images")
        refresh_id = session.scalar(
            select(ArticleRefresh.id).where(ArticleRefresh.article_id == case.article_id)
        )
    assert_approved_publication(client, database, case)
    with database.begin() as session:
        if withdrawal == "source":
            article = session.get(Article, case.article_id)
            review_source(
                session,
                article.origins[0].source_id,
                SourceDecision(decision="rejected", note="Explicit withdrawal"),
            )
        else:
            editorial.decide_article(
                session,
                case.article_id,
                editorial.EditorialDecision(
                    action=withdrawal, actor="Editor", note="Explicit withdrawal"
                ),
            )
    assert client.get(case.path).status_code == 404
    assert str(case.article_id) not in {
        item["id"] for item in client.get("/v1/feed?languages=en").json()["items"]
    }
    assert try_promote_refresh(database, refresh_id) != "promoted"
    assert client.get(case.path).status_code == 404


def test_ready_image_replacement_invalidates_cache_once_without_republishing(
    database, client, published_retry_case, monkeypatch
):
    case = published_retry_case
    monkeypatch.setattr(get_settings(), "image_storage_enabled", False)
    replacement = "https://publisher.example/replacement-image.png"
    with database.begin() as session:
        case.request(session, "images")
        owner = session.scalar(
            select(ArticleRefresh).where(ArticleRefresh.article_id == case.article_id)
        )
        refresh_id = owner.id
        job = session.get(ArticleImageJob, owner.discovery_job_id)
        owner.image_url, job.image_url = replacement, replacement
        touch_refresh(owner)
        finish_job(job, "found", utcnow())
    # A complete private result alone cannot leak into a warm approved response.
    assert_approved_publication(client, database, case)
    assert try_promote_refresh(database, refresh_id) == "promoted"
    response = client.get(case.path)
    assert response.status_code == 200 and response.headers["x-cache"] == "MISS"
    assert response.json() == {**case.body, "image_url": replacement}
    assert client.get(case.path).headers["x-cache"] == "HIT"
    with database() as session:
        owner = session.get(ArticleRefresh, refresh_id)
        article = session.get(Article, case.article_id)
        assert owner.status == "promoted" and owner.completed_at is not None
        assert article.slug == case.slug and article.published_to_feed_at == case.published_at
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 1


def test_retrying_a_new_unpublished_article_never_exposes_it(
    database, client, published_retry_case
):
    with database.begin() as session:
        draft = make_article(session)
        identifier, slug = draft.id, draft.slug
        previous = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == identifier)
        )
        request_processing_retry(
            session,
            identifier,
            target="analysis",
            expected_revision=draft.editorial_revision,
            expected_job_id=previous.id,
            actor={"subject": "draft-editor"},
        )
        assert (
            session.scalar(select(ArticleRefresh.id).where(ArticleRefresh.article_id == identifier))
            is None
        )
    assert client.get(f"/v1/articles/{slug}").status_code == 404
    assert str(identifier) not in {
        item["id"] for item in client.get("/v1/feed?languages=en").json()["items"]
    }


def fresh_article_inventory(client):
    import json

    storage = cache.get_sitemap_cache()
    key = storage.namespace + ":sitemaps:v1:manifest"
    previous = storage.redis.get(key)
    if previous:
        storage.redis.set(key, json.dumps(dict(json.loads(previous), built_at=0)), ex=3900)
    sitemaps.refresh_snapshot()
    manifest = client.get("/v1/sitemaps").json()
    return manifest["generation"], {
        path
        for part in manifest["parts"]
        if part["kind"] == "articles"
        for path in client.get(
            f"/v1/sitemaps/articles/{part['page']}?v={manifest['generation']}"
        ).json()["paths"]
    }


@pytest.mark.parametrize("target", ["analysis", "enrichment"])
def test_real_private_workers_swap_cached_publication_without_new_feed_event(
    database, client, published_retry_case, monkeypatch, target
):
    from devfeed_aggregator import analysis_tasks

    case = published_retry_case
    with database() as session:
        event = session.get(FeedNotificationEvent, case.article_id)
        original_event = {
            column.name: getattr(event, column.name)
            for column in FeedNotificationEvent.__table__.columns
        }
    old_generation, old_paths = fresh_article_inventory(client)
    assert f"/articles/{case.slug}" in old_paths
    with database.begin() as session:
        case.request(session, target)
    assert_approved_publication(client, database, case)
    if target == "enrichment":
        support.complete_private_enrichment(database, monkeypatch, case.article_id)
        # Publisher labels may invalidate collection caches; the served article stays approved.
        assert client.get(case.path).json() == case.body
    assert_approved_publication(client, database, case)

    def during_specialist(_call):
        assert_approved_publication(client, database, case)

    refresh_id, job_id, calls = workers_support.complete_private_analysis(
        database, monkeypatch, case.article_id, before_complete=during_specialist
    )
    assert calls
    after = client.get(case.path)
    assert after.status_code == 200 and after.headers["x-cache"] == "MISS"
    assert after.json()["ai_summary"] == "Inspect Python tasks with a complete private replacement."
    assert after.json()["id"] == str(case.article_id)
    assert after.json()["slug"] == case.slug
    assert client.get(case.path).headers["x-cache"] == "HIT"
    analysis_tasks._analyze(job_id)
    assert client.get(case.path).headers["x-cache"] == "HIT"
    generation, paths = fresh_article_inventory(client)
    assert generation != old_generation and f"/articles/{case.slug}" in paths
    assert str(case.article_id) in {
        item["id"] for item in client.get("/v1/feed?languages=en").json()["items"]
    }
    with database() as session:
        assert session.get(ArticleRefresh, refresh_id).status == "promoted"
        article = session.get(Article, case.article_id)
        assert article.published_to_feed_at == case.published_at and article.slug == case.slug
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 1
        event = session.get(FeedNotificationEvent, case.article_id)
        assert {
            column.name: getattr(event, column.name)
            for column in FeedNotificationEvent.__table__.columns
        } == original_event
