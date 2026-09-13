"""Opt-in PostgreSQL/Redis coverage; never use the development database."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_aggregator import image_tasks, scheduler, tasks
from devfeed_aggregator.queue import get_queue
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.image_jobs import backfill_images, claim_image, request_image, retry_image
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import Article, ArticleImageJob, IngestionJob, Source, utcnow
from devfeed_core.urls import fingerprint
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import SimpleWorker
from rq.serializers import JSONSerializer
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def article(database, *, image_url=None):
    identifier = uuid.uuid4()
    url = f"https://publisher.example/{identifier}"
    with database.begin() as session:
        session.add(
            Article(
                id=identifier,
                canonical_url=url,
                url_hash=fingerprint(url),
                title="Example",
                image_url=image_url,
                metadata_source_type="publisher",
            )
        )
    return identifier


def test_feed_ingestion_queues_missing_images_atomically_and_keeps_publisher_images(
    database, rss_bytes, monkeypatch
):
    monkeypatch.setattr(tasks, "fetch_feed", lambda *args: FetchResult(200, rss_bytes, args[0]))
    with database.begin() as session:
        source = Source(
            approval_status="approved",
            name="Publisher",
            source_type="publisher",
            feed_url="https://publisher.example/rss",
        )
        session.add(source)
        session.flush()
        identifier = request_ingestion(session, source).id
    tasks.ingest(str(identifier))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 2
        jobs = session.scalars(select(ArticleImageJob)).all()
        assert len(jobs) == 1
        assert session.get(Article, jobs[0].article_id).image_url is None
    # Completed feed entries are not reimported; image jobs stay distinct.
    tasks.ingest(str(identifier))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 1


def test_failed_ingestion_rolls_back_image_outbox_together_with_articles(
    database, rss_bytes, monkeypatch
):
    monkeypatch.setattr(tasks, "fetch_feed", lambda *args: FetchResult(200, rss_bytes, args[0]))
    with database.begin() as session:
        source = Source(
            approval_status="approved",
            name="Example",
            source_type="aggregator",
            feed_url="https://example.com/rss",
        )
        session.add(source)
        session.flush()
        identifier = request_ingestion(session, source).id
    store = tasks.store_entries

    def fail(*args):
        store(*args)
        raise RuntimeError("rollback")

    monkeypatch.setattr(tasks, "store_entries", fail)
    tasks.ingest(str(identifier))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 0
        assert session.get(IngestionJob, identifier).status == "queued"


def test_real_rq_enrichment_updates_api_without_changing_other_metadata(
    database, client, monkeypatch, publish_for_read_test
):
    identifier = article(database)
    publish_for_read_test([identifier])
    with database.begin() as session:
        image_job = request_image(session, identifier).id
        original = session.get(Article, identifier)
        stable = (original.title, original.canonical_url, original.feed_at, original.published_at)
    monkeypatch.setattr(
        image_tasks,
        "fetch_page",
        lambda url: FetchResult(200, b'<meta property="og:image" content="/cover.png">', url),
    )
    assert scheduler.tick()["images_dispatched"] == 1
    queue = get_queue("images")
    try:
        SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(
            burst=True
        )
    finally:
        queue.connection.close()
    with database() as session:
        saved = session.get(Article, identifier)
        assert saved.image_url == "https://publisher.example/cover.png"
        assert (saved.title, saved.canonical_url, saved.feed_at, saved.published_at) == stable
        assert session.get(ArticleImageJob, image_job).outcome == "found"
    assert (
        client.get(f"/v1/articles/{identifier}").json()["image_url"]
        == "https://publisher.example/cover.png"
    )
    assert (
        client.get("/v1/feed").json()["items"][0]["image_url"]
        == "https://publisher.example/cover.png"
    )


def test_backfill_is_bounded_skips_existing_images_and_does_not_repeat_no_image(
    database, monkeypatch
):
    ids = [article(database) for _ in range(3)]
    article(database, image_url="https://cdn.example/present.png")
    with database.begin() as session:
        batch = backfill_images(session, 2)
        first = [job.id for job in batch]
    assert len(first) == 2
    monkeypatch.setattr(
        image_tasks, "fetch_page", lambda url: FetchResult(200, b"<html>No preview</html>", url)
    )
    for identifier in first:
        image_tasks.enrich_image(str(identifier))
    with database.begin() as session:
        assert len(backfill_images(session, 100)) == 1
    with database.begin() as session:
        assert backfill_images(session, 100) == []
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == len(ids)


def test_concurrent_requests_and_claims_coalesce(database):
    identifier = article(database)

    def request(_):
        with database.begin() as session:
            return request_image(session, identifier).id

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(request, range(3)))
    assert len(set(jobs)) == 1

    def claim(_):
        with database.begin() as session:
            return claim_image(session, jobs[0]) is not None

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(claim, range(3))) == 1


def test_stale_image_worker_cannot_commit(database, monkeypatch):
    identifier = article(database)
    with database.begin() as session:
        image_job = request_image(session, identifier).id

    def fetch(url):
        with database.begin() as session:
            session.get(ArticleImageJob, image_job).lease_token = uuid.uuid4()
        return FetchResult(200, b'<meta property="og:image" content="/cover.png">', url)

    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    image_tasks.enrich_image(str(image_job))
    with database() as session:
        assert session.get(Article, identifier).image_url is None
        assert session.get(ArticleImageJob, image_job).status == "running"


def test_image_terminal_failure_does_not_touch_source_and_retry_preserves_history(
    database, monkeypatch
):
    identifier = article(database)
    with database.begin() as session:
        image_job = request_image(session, identifier).id

    def fetch(url):
        raise FeedError("not found", status=404, reason="http_error")

    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    image_tasks.enrich_image(str(image_job))
    with database.begin() as session:
        assert session.get(ArticleImageJob, image_job).status == "failed"
        new_job = retry_image(session, image_job)
        assert new_job.id != image_job and new_job.article_id == identifier
        assert session.get(ArticleImageJob, image_job).http_status == 404


def test_image_outbox_survives_broker_loss_and_recovers_lease(database, monkeypatch):
    identifier = article(database)
    with database.begin() as session:
        image_job = request_image(session, identifier).id
    queue = get_queue("images")

    def unavailable(*args, **kwargs):
        raise RedisConnectionError("offline")

    with monkeypatch.context() as patch:
        patch.setattr(queue, "enqueue", unavailable)
        patch.setattr(scheduler, "get_queue", lambda name="ingestion": queue)
        with pytest.raises(RedisConnectionError):
            scheduler.tick()
    with database() as session:
        assert session.get(ArticleImageJob, image_job).dispatched_at is None
    assert scheduler.tick()["images_dispatched"] == 1
    get_queue("images").empty()
    with database.begin() as session:
        session.get(ArticleImageJob, image_job).dispatched_at = utcnow() - timedelta(minutes=6)
    assert scheduler.tick()["images_dispatched"] == 1
    with database.begin() as session:
        claimed, _ = claim_image(session, image_job)
        claimed.lease_until = utcnow() - timedelta(seconds=1)
    assert scheduler.tick()["images_recovered"] == 1
    with database() as session:
        saved = session.get(ArticleImageJob, image_job)
        assert saved.status == "queued" and saved.lease_token is None
