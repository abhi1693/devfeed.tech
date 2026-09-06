"""Application cache behavior with explicitly supplied disposable PostgreSQL/Redis."""

import uuid

import pytest
from devfeed_aggregator import image_tasks, source_tasks, tasks
from devfeed_core import cache, services
from devfeed_core.cache_events import DIRTY
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import ArticleImageJob, Source, utcnow
from devfeed_core.schemas import SourceDecision
from devfeed_core.source_enrichment import request_enrichment
from sqlalchemy import event, select, update
from sqlalchemy.dialects.postgresql import insert

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def enable_cache(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    yield
    cache.close_cache()


def approved_source(database):
    with database.begin() as session:
        source = Source(
            name="Example",
            feed_url="https://example.com/rss",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        return source.id


def test_repeated_get_performs_no_sql_on_hit(client):
    statements = []

    def capture(*args):
        statements.append(args[2])

    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        assert client.get("/v1/feed").headers["x-cache"] == "MISS"
        count = len(statements)
        assert count > 0
        assert client.get("/v1/feed").headers["x-cache"] == "HIT"
        assert len(statements) == count
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)


def test_api_taxonomy_write_invalidates_cached_reads(client):
    assert client.get("/v1/tags").json() == []
    assert client.get("/v1/tags").headers["x-cache"] == "HIT"
    assert client.post("/v1/tags", json={"name": "Python", "slug": "python"}).status_code == 201
    result = client.get("/v1/tags")
    assert result.headers["x-cache"] == "MISS" and result.json()[0]["slug"] == "python"


def test_review_commit_invalidates_but_rollback_and_polling_do_not(database, client):
    identifier = approved_source(database)
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    with database.begin() as session:
        session.get(Source, identifier).last_success_at = utcnow()
    assert client.get(path).headers["x-cache"] == "HIT"
    with database() as session:
        services.review_source(
            session, identifier, SourceDecision(decision="rejected", note="test")
        )
        session.rollback()
    assert client.get(path).headers["x-cache"] == "HIT"
    with database.begin() as session:
        services.review_source(
            session, identifier, SourceDecision(decision="rejected", note="test")
        )
    assert client.get(path).status_code == 404


def test_worker_ingestion_images_and_profiles_invalidate_cached_reads(
    database, client, rss_bytes, monkeypatch, publish_for_read_test
):
    identifier = approved_source(database)
    assert client.get("/v1/feed").json()["items"] == []
    with database.begin() as session:
        source = session.get(Source, identifier)
        job_id = request_ingestion(session, source).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(str(job_id))
    assert client.get("/v1/feed").json()["items"] == []
    publish_for_read_test()
    feed = client.get("/v1/feed")
    assert feed.headers["x-cache"] == "MISS" and len(feed.json()["items"]) == 2
    with database() as session:
        image_job = session.scalar(select(ArticleImageJob))
        image_id, article_id = image_job.id, image_job.article_id
    path = f"/v1/articles/{article_id}"
    assert client.get(path).json()["image_url"] is None
    monkeypatch.setattr(
        image_tasks,
        "fetch_page",
        lambda url: FetchResult(
            200, b'<meta property="og:image" content="https://example.com/cover.png">', url
        ),
    )
    image_tasks.enrich_image(str(image_id))
    assert client.get(path).json()["image_url"] == "https://example.com/cover.png"
    source_path = f"/v1/sources/{identifier}"
    assert client.get(source_path).json()["logo_url"] is None
    with database.begin() as session:
        profile_job = request_enrichment(session, identifier).id
    monkeypatch.setattr(
        source_tasks,
        "lookup_profile",
        lambda *args: ({"logo_url": "https://example.com/logo.png"}, None),
    )
    source_tasks.enrich_source(str(profile_job))
    assert client.get(source_path).json()["logo_url"] == "https://example.com/logo.png"


def test_redis_lua_fences_old_generations_and_lock_owners():
    store = cache.get_cache()
    owner = store.lookup(str(uuid.uuid4()), "public")
    assert store.publish(owner, b"[]", 10)
    assert store.redis.ttl(owner.key) > 0
    store.invalidate()
    assert not store.publish(owner, b'["old"]', 10)
    # Another loader replacing an expired token must not be unlocked by old work.
    store.redis.set(owner.lock_key, "replacement", ex=10)
    store.release(owner)
    assert store.redis.get(owner.lock_key) == b"replacement"


def test_duplicate_upsert_and_zero_row_update_do_not_invalidate(database, client):
    identifier = approved_source(database)
    path = f"/v1/sources/{identifier}"
    assert client.get(path).status_code == 200
    with database.begin() as session:
        result = session.scalar(
            insert(Source)
            .values(name="Duplicate", feed_url="https://example.com/rss", source_type="publisher")
            .on_conflict_do_nothing(index_elements=[Source.feed_url])
            .returning(Source.id)
        )
        assert result is None and DIRTY not in session.info
        result = session.scalar(
            update(Source)
            .where(Source.id == uuid.uuid4())
            .values(name="Unmatched")
            .returning(Source.id)
        )
        assert result is None and DIRTY not in session.info
    assert client.get(path).headers["x-cache"] == "HIT"
    with database.begin() as session:
        result = session.scalar(
            update(Source)
            .where(Source.id == identifier)
            .values(name="Changed")
            .returning(Source.id)
        )
        assert result == identifier and session.info[DIRTY]
    response = client.get(path)
    assert response.headers["x-cache"] == "MISS" and response.json()["name"] == "Changed"
