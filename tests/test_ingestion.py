import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler, tasks
from devfeed_aggregator.queue import get_queue
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.jobs import MAX_ATTEMPTS, claim_job, request_ingestion
from devfeed_core.models import Article, ArticleOrigin, Category, IngestionJob, Source, utcnow
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import SimpleWorker
from rq.serializers import JSONSerializer
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def make_job(database, url="https://example.com/rss", source_type="publisher"):
    with database.begin() as session:
        source = Source(
            name="Example", feed_url=url, source_type=source_type, approval_status="approved"
        )
        session.add(source)
        session.flush()
        job = request_ingestion(session, source)
        return source.id, job.id


def test_ingestion_deduplicates_across_runs_and_sources(
    database, rss_bytes, monkeypatch, configured_tags
):
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0], '"v1"'))
    with database.begin() as session:
        session.add(Category(name="Backend", slug="backend", keywords=["fastapi"]))
    source_id, job_id = make_job(database)
    tasks.ingest(str(job_id))
    tasks.ingest(str(job_id))  # Duplicate queue delivery after success must be a no-op.
    with database.begin() as session:
        source = session.get(Source, source_id)
        assert source.etag == '"v1"'
        next_job = request_ingestion(session, source)
        next_id = next_job.id
    tasks.ingest(str(next_id))
    _, other_id = make_job(database, "https://another.example/rss")
    tasks.ingest(str(other_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 4
        first = session.get(IngestionJob, job_id)
        assert (first.status, first.articles_created, first.entries_skipped) == ("succeeded", 2, 1)
        assert session.get(IngestionJob, next_id).articles_created == 0
        python = session.scalar(select(Article).where(Article.title.like("%Python%")))
        assert python.categories[0].slug == "backend"
        assert sorted(tag.slug for tag in python.tags) == ["fastapi", "postgresql", "python"]
        assert python.content_type == "tutorial"


def test_changed_guid_url_does_not_create_orphan_article(database, rss_bytes, monkeypatch):
    _, job_id = make_job(database)
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(str(job_id))
    changed = rss_bytes.replace(b"https://example.com/python?", b"https://example.com/renamed?")
    with database.begin() as session:
        source = session.scalar(select(Source))
        second_id = request_ingestion(session, source).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, changed, a[0]))
    tasks.ingest(str(second_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 2


def test_not_modified_keeps_validators_and_marks_success(database, monkeypatch):
    source_id, job_id = make_job(database)
    with database.begin() as session:
        session.get(Source, source_id).etag = '"existing"'
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(304, b"", a[0]))
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.get(Source, source_id).etag == '"existing"'
        assert session.get(Source, source_id).last_success_at is not None
        assert session.get(IngestionJob, job_id).http_status == 304


def test_transient_errors_retry_then_back_off_source(database, monkeypatch):
    source_id, job_id = make_job(database)

    def unavailable(*args):
        raise FeedError("HTTP 503", retryable=True, status=503, retry_after=120)

    monkeypatch.setattr(tasks, "fetch_feed", unavailable)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        tasks.ingest(str(job_id))
        with database.begin() as session:
            job = session.get(IngestionJob, job_id)
            assert job.attempts == attempt
            assert job.status == ("failed" if attempt == MAX_ATTEMPTS else "queued")
            if attempt < MAX_ATTEMPTS:
                assert job.available_at > utcnow() + timedelta(seconds=110)
                job.available_at = utcnow() - timedelta(seconds=1)
    with database() as session:
        source = session.get(Source, source_id)
        assert source.consecutive_failures == 1
        assert source.next_fetch_at > utcnow() + timedelta(seconds=3500)


def test_permanent_failure_is_visible_and_not_retried(database, monkeypatch):
    _, job_id = make_job(database)

    def missing(*args):
        raise FeedError("HTTP 404", status=404)

    monkeypatch.setattr(tasks, "fetch_feed", missing)
    tasks.ingest(str(job_id))
    with database() as session:
        job = session.get(IngestionJob, job_id)
        assert (job.status, job.attempts, job.http_status) == ("failed", 1, 404)


def test_database_failure_rolls_back_articles_and_retries(database, rss_bytes, monkeypatch):
    _, job_id = make_job(database)
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    original = tasks.store_entries

    def crash_after_store(*args):
        original(*args)
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(tasks, "store_entries", crash_after_store)
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        job = session.get(IngestionJob, job_id)
        assert job.status == "queued"
        assert job.error == "Ingestion error: RuntimeError"


def test_disabled_source_does_not_fetch(database, monkeypatch):
    source_id, job_id = make_job(database)
    with database.begin() as session:
        session.get(Source, source_id).enabled = False
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: pytest.fail("Disabled source fetched"))
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.get(IngestionJob, job_id).status == "failed"


def test_expired_worker_lease_is_recovered(database):
    _, job_id = make_job(database)
    with database.begin() as session:
        job, _ = claim_job(session, job_id)
        job.lease_until = utcnow() - timedelta(seconds=1)
    assert scheduler.tick()["recovered"] == 1
    with database() as session:
        job = session.get(IngestionJob, job_id)
        assert job.status == "queued"
        assert job.lease_token is None
        assert job.available_at > utcnow()


def test_stale_owner_cannot_commit(database, rss_bytes, monkeypatch):
    _, job_id = make_job(database)

    def replaced_worker(*args):
        with database.begin() as session:
            session.get(IngestionJob, job_id).lease_token = uuid.uuid4()
        return FetchResult(200, rss_bytes, args[0])

    monkeypatch.setattr(tasks, "fetch_feed", replaced_worker)
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0


def test_only_one_worker_can_claim_a_job(database):
    _, job_id = make_job(database)

    def claim(_):
        with database.begin() as session:
            return claim_job(session, job_id) is not None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(4))) == 1


def test_scheduler_publishes_durable_jobs_to_real_rq(database, rss_bytes, monkeypatch):
    with database.begin() as session:
        session.add(
            Source(
                name="Example",
                feed_url="https://example.com/rss",
                source_type="publisher",
                approval_status="approved",
            )
        )
    result = scheduler.tick()
    assert result == {
        "scheduled": 1,
        "dispatched": 1,
        "recovered": 0,
        "images_dispatched": 0,
        "images_recovered": 0,
        "articles_dispatched": 0,
        "articles_recovered": 0,
        "profiles_dispatched": 0,
        "profiles_recovered": 0,
        "analyses_dispatched": 0,
        "analyses_recovered": 0,
        "notifications_dispatched": 0,
        "notifications_recovered": 0,
    }
    assert scheduler.tick() == {
        "scheduled": 0,
        "dispatched": 0,
        "recovered": 0,
        "images_dispatched": 0,
        "images_recovered": 0,
        "articles_dispatched": 0,
        "articles_recovered": 0,
        "profiles_dispatched": 0,
        "profiles_recovered": 0,
        "analyses_dispatched": 0,
        "analyses_recovered": 0,
        "notifications_dispatched": 0,
        "notifications_recovered": 0,
    }
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    queue = get_queue()
    SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(burst=True)
    with database() as session:
        assert session.scalar(select(IngestionJob.status)) == "succeeded"
        assert session.scalar(select(func.count()).select_from(Article)) == 2


def test_broker_outage_keeps_job_for_later_dispatch(database, monkeypatch):
    _, job_id = make_job(database)
    queue = get_queue()

    def fail_publish(*args, **kwargs):
        raise RedisConnectionError("simulated broker outage")

    with monkeypatch.context() as patch:
        patch.setattr(queue, "enqueue", fail_publish)
        patch.setattr(scheduler, "get_queue", lambda: queue)
        with pytest.raises(RedisConnectionError):
            scheduler.tick()
    with database() as session:
        assert session.get(IngestionJob, job_id).dispatched_at is None
    assert scheduler.tick()["dispatched"] == 1


def test_lost_redis_message_is_dispatched_again(database):
    _, job_id = make_job(database)
    scheduler.tick()
    get_queue().empty()
    with database.begin() as session:
        session.get(IngestionJob, job_id).dispatched_at = utcnow() - timedelta(minutes=6)
    assert scheduler.tick()["dispatched"] == 1
    assert get_queue().count == 1


def test_concurrent_scheduler_runs_create_one_active_job(database):
    with database.begin() as session:
        session.add(
            Source(
                name="Example",
                feed_url="https://example.com/rss",
                source_type="publisher",
                approval_status="approved",
            )
        )
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: scheduler.tick(), range(3)))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
    assert get_queue().count == 1


@pytest.mark.parametrize("types", [("aggregator", "publisher"), ("publisher", "aggregator")])
def test_publisher_metadata_wins_over_discovery_without_losing_provenance(
    database, rss_bytes, monkeypatch, types
):
    monkeypatch.setattr(tasks, "fetch_feed", lambda *args: FetchResult(200, rss_bytes, args[0]))
    _, first_job = make_job(database, "https://first.example/rss", source_type=types[0])
    tasks.ingest(str(first_job))
    with database() as session:
        first = session.scalar(select(Article).where(Article.title.like("%Python%")))
        article_id, feed_at = first.id, first.feed_at
        if types[0] == "aggregator":
            assert first.author is first.published_at is first.image_url is first.language is None
            assert first.summary == ""
    _, second_job = make_job(database, "https://second.example/rss", source_type=types[1])
    tasks.ingest(str(second_job))
    tasks.ingest(str(second_job))
    with database() as session:
        article = session.get(Article, article_id)
        assert article.author == "Example Author"
        assert article.summary == "A practical PostgreSQL tutorial."
        assert article.image_url == "https://example.com/cover.jpg"
        assert article.published_at is not None
        # This tiny technical excerpt does not justify inheriting the feed's en hint.
        assert article.language is None
        assert article.metadata_source_type == "publisher"
        assert article.feed_at == feed_at
        assert len(article.origins) == 2
        assert session.scalar(select(func.count()).select_from(Article)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 4
        origins = {origin.source.source_type: origin.source_metadata for origin in article.origins}
        assert origins["publisher"]["author"] == "Example Author"
        assert origins["aggregator"]["submitter"] == "Example Author"
        assert origins["aggregator"]["tags"] == ["python"]
