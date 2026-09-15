import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.image_jobs import backfill_storage, request_image
from devfeed_core.models import Article, ArticleImageJob, utcnow
from devfeed_core.schemas import ArticleOut
from devfeed_core.urls import fingerprint

pytestmark = pytest.mark.integration


@pytest.fixture
def enabled(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    get_settings.cache_clear()
    monkeypatch.setattr(
        image_storage_tasks, "storage_client", lambda: SimpleNamespace(close=lambda: None)
    )
    return database


def article(factory):
    identifier = uuid.uuid4()
    url = f"https://publisher.test/{identifier}"
    with factory.begin() as session:
        session.add(
            Article(
                id=identifier,
                canonical_url=url,
                url_hash=fingerprint(url),
                title="Image",
                image_url=url + ".png",
            )
        )
    return identifier, url + ".png"


def test_storage_retries_resume_and_reader_switches_only_when_complete(enabled, monkeypatch):
    factory = enabled
    identifier, source = article(factory)
    original_calls, variants = [], []

    def original(client, url):
        original_calls.append(url)
        return {
            "hash": "hash",
            "original_key": "originals/hash",
            "source_url": url,
            "source_width": 1200,
            "version": "v1",
            "variants": [],
        }

    fail = True

    def variant(client, asset, width):
        nonlocal fail
        variants.append(width)
        if width == 640 and fail:
            fail = False
            raise FeedError("retry", reason="image_transform", retryable=True)
        return {"key": f"thumbnails/v1/hash/{width}.webp", "width": width}

    monkeypatch.setattr(image_storage_tasks, "store_original", original)
    monkeypatch.setattr(image_storage_tasks, "store_variant", variant)
    with factory.begin() as session:
        job_id = request_image(session, identifier).id
    image_tasks.enrich_image(str(job_id))
    with factory.begin() as session:
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "queued"
        assert len(job.storage["variants"]) == 1
        assert ArticleOut.from_article(session.get(Article, identifier)).image_url == source
        job.available_at = utcnow() - timedelta(seconds=1)
    image_tasks.enrich_image(str(job_id))
    with factory.begin() as session:
        saved = session.get(Article, identifier)
        assert saved.image_url == source
        output = ArticleOut.from_article(saved)
        assert output.image_url == "https://images.test/thumbnails/v1/hash/960.webp"
        assert len(output.image_variants) == 3
        assert session.get(ArticleImageJob, job_id).status == "succeeded"
        assert backfill_storage(session, 100) == []
    assert original_calls == [source]
    assert variants == [320, 640, 640, 960]


def test_source_change_during_upload_does_not_publish_stale_image(enabled, monkeypatch):
    identifier, source = article(enabled)
    with enabled.begin() as session:
        job_id = request_image(session, identifier).id

    def original(client, url):
        with enabled.begin() as session:
            session.get(Article, identifier).image_url = "https://publisher.test/replacement.png"
        return {
            "hash": "hash",
            "original_key": "originals/hash",
            "source_url": url,
            "source_width": 100,
            "version": "v1",
            "variants": [],
        }

    monkeypatch.setattr(image_storage_tasks, "store_original", original)
    monkeypatch.setattr(
        image_storage_tasks, "store_variant", lambda *a: {"key": "key", "width": 100}
    )
    image_tasks.enrich_image(str(job_id))
    with enabled.begin() as session:
        assert session.get(Article, identifier).managed_image == {}
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        assert len(backfill_storage(session, 100)) == 1


def test_storage_backfill_skips_active_and_failed_attempts(enabled):
    first, _ = article(enabled)
    article(enabled)
    with enabled.begin() as session:
        jobs = backfill_storage(session, 1)
        assert len(jobs) == 1
        jobs[0].status = "failed"
    with enabled.begin() as session:
        assert len(backfill_storage(session, 10)) == 1
    with enabled.begin() as session:
        assert backfill_storage(session, 10) == []


def test_discovery_queues_storage_in_same_images_pipeline(enabled, monkeypatch):
    from devfeed_core.feeds.fetcher import FetchResult
    from sqlalchemy import select

    identifier, _ = article(enabled)
    with enabled.begin() as session:
        session.get(Article, identifier).image_url = None
        job_id = request_image(session, identifier).id
    monkeypatch.setattr(
        image_tasks,
        "fetch_page",
        lambda url: FetchResult(
            200, b'<meta property="og:image" content="https://publisher.test/cover.png">', url
        ),
    )
    image_tasks.enrich_image(str(job_id))
    with enabled() as session:
        jobs = session.scalars(
            select(ArticleImageJob)
            .where(ArticleImageJob.article_id == identifier)
            .order_by(ArticleImageJob.created_at)
        ).all()
        assert [(job.operation, job.status) for job in jobs] == [
            ("discover", "succeeded"),
            ("store", "queued"),
        ]


def test_explicit_retry_keeps_successful_upload_progress(enabled):
    from devfeed_core.image_jobs import retry_image

    identifier, source = article(enabled)
    progress = {"source_url": source, "original_key": "originals/hash", "variants": []}
    with enabled.begin() as session:
        job = request_image(session, identifier)
        previous = job.id
        job.storage = progress
        job.status = "failed"
    with enabled.begin() as session:
        retry = retry_image(session, previous)
        assert retry.id != previous
        assert retry.operation == "store"
        assert retry.storage == progress


@pytest.mark.parametrize(
    "source_type,summary,ai_enabled",
    [
        ("aggregator", "Feed summary", False),
        ("publisher", "", False),
        ("publisher", "Feed summary", True),
    ],
)
@pytest.mark.parametrize("feed_image", [None, "https://publisher.test/feed.png"])
def test_page_enrichment_does_not_race_discovery_before_storage(
    enabled, monkeypatch, source_type, summary, ai_enabled, feed_image
):
    from devfeed_aggregator import article_tasks, tasks
    from devfeed_core.feeds.fetcher import FetchResult
    from devfeed_core.feeds.parser import ParsedArticle, ParsedFeed
    from devfeed_core.models import ArticleEnrichmentJob, Source
    from devfeed_core.source_types import SourceType
    from sqlalchemy import select

    monkeypatch.setattr(get_settings(), "ai_enabled", ai_enabled)
    parsed = ParsedFeed(
        entries=[
            ParsedArticle(
                entry_key="article",
                canonical_url="https://publisher.test/article",
                title="Example",
                summary=summary,
                author=None,
                published_at=None,
                tags=[],
                source_type=SourceType(source_type),
                feed_at=utcnow(),
                source_metadata={},
                image_url=feed_image,
            )
        ],
        seen=1,
        skipped=0,
    )
    with enabled.begin() as session:
        source = Source(
            name="Publisher",
            source_type=source_type,
            feed_url="https://publisher.test/feed",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        source_id = source.id
        assert tasks.store_entries(session, source_id, parsed) == 1
        article_job = session.scalar(select(ArticleEnrichmentJob))
        enrichment_id, article_id = article_job.id, article_job.article_id
        images = session.scalars(select(ArticleImageJob)).all()
        assert [job.operation for job in images] == (["store"] if feed_image else [])

    # Complete the page job first, without running any image worker.
    monkeypatch.setattr(
        article_tasks,
        "fetch_article_page",
        lambda url: FetchResult(
            200, b'<html><head><meta property="og:image" content="/page.png"></head></html>', url
        ),
    )
    article_tasks.enrich_article(str(enrichment_id))
    with enabled.begin() as session:
        assert session.get(ArticleEnrichmentJob, enrichment_id).status == "succeeded"
        jobs = session.scalars(select(ArticleImageJob)).all()
        assert len(jobs) == 1
        assert jobs[0].operation == "store" and jobs[0].status == "queued"
        assert jobs[0].image_url == (feed_image or "https://publisher.test/page.png")
        storage_id = jobs[0].id
        # An already-imported origin is skipped; storage must already be queued.
        assert tasks.store_entries(session, source_id, parsed) == 0

    monkeypatch.setattr(
        image_storage_tasks,
        "store_original",
        lambda client, url: {
            "hash": "hash",
            "original_key": "originals/hash",
            "source_url": url,
            "source_width": 320,
            "version": "v1",
            "variants": [],
        },
    )
    monkeypatch.setattr(
        image_storage_tasks,
        "store_variant",
        lambda client, asset, width: {
            "key": f"thumbnails/v1/hash/{width}.webp",
            "width": width,
        },
    )
    image_tasks.enrich_image(str(storage_id))
    with enabled() as session:
        assert session.get(ArticleImageJob, storage_id).status == "succeeded"
        assert ArticleOut.from_article(session.get(Article, article_id)).image_variants
