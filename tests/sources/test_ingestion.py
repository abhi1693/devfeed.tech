import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler, tasks
from devfeed_aggregator.queue import get_queue
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.jobs import MAX_ATTEMPTS, claim_job, request_ingestion
from devfeed_core.models import Article, ArticleOrigin, IngestionJob, Source, Topic, utcnow
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
        session.add(
            Topic(
                kind="discipline",
                status="active",
                name="Backend",
                slug="backend",
                keywords=["fastapi"],
            )
        )
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
        assert python.topic_links[0].topic.slug == "backend"
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

    def crash_after_store(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(tasks, "store_entries", crash_after_store)
    tasks.ingest(str(job_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        job = session.get(IngestionJob, job_id)
        assert job.status == "queued"
        assert job.error == "Ingestion error: RuntimeError"


def test_missing_attempt_timestamp_rolls_back_success_and_retries(database, monkeypatch):
    source_id, job_id = make_job(database)

    def response_without_attempt(url, *_args):
        # Claiming set the timestamp before transport released its transaction.
        # Simulate that state being removed before the response is applied.
        with database.begin() as session:
            session.get(Source, source_id).last_attempt_at = None
        return FetchResult(304, b"", url)

    monkeypatch.setattr(tasks, "fetch_feed", response_without_attempt)

    tasks.ingest(str(job_id))

    with database() as session:
        job = session.get(IngestionJob, job_id)
        source = session.get(Source, source_id)
        assert job.status == "queued" and job.attempts == 1
        assert job.error == "Ingestion error: RuntimeError"
        assert job.available_at > utcnow()
        assert job.lease_token is job.lease_until is None
        assert job.http_status is None
        assert source.last_success_at is None and source.last_attempt_at is None


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
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 0,
        "relationship_scans_completed": 0,
        "tags_scanned": 0,
        "tags_linked": 0,
        "tags_unlinked": 0,
        "tags_ambiguous": 0,
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
        "topic_analyses_dispatched": 0,
        "topic_analyses_recovered": 0,
        "verifications_scheduled": 0,
        "verifications_dispatched": 0,
        "verifications_recovered": 0,
        "recommendation_users_queued": 0,
        "recommendations_dispatched": 0,
        "partner_pipeline_dispatched": 0,
        "partner_evaluations_dispatched": 0,
        "notifications_dispatched": 0,
        "notifications_recovered": 0,
    }
    assert scheduler.tick() == {
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 0,
        "relationship_scans_completed": 0,
        "tags_scanned": 0,
        "tags_linked": 0,
        "tags_unlinked": 0,
        "tags_ambiguous": 0,
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
        "topic_analyses_dispatched": 0,
        "topic_analyses_recovered": 0,
        "verifications_scheduled": 0,
        "verifications_dispatched": 0,
        "verifications_recovered": 0,
        "recommendation_users_queued": 0,
        "recommendations_dispatched": 0,
        "partner_pipeline_dispatched": 0,
        "partner_evaluations_dispatched": 0,
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
        patch.setattr(scheduler, "get_queue", lambda name="ingestion": queue)
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


def test_known_feed_entries_skip_language_work_and_use_bounded_origin_reads(database, monkeypatch):
    from dataclasses import replace

    from devfeed_core.db import get_engine
    from sqlalchemy import event

    entries = "".join(
        f"<item><guid>known-{index}</guid><title>Engineering article {index}</title>"
        f"<link>https://example.com/article-{index}</link>"
        "<description>A sufficiently useful publisher summary.</description></item>"
        for index in range(500)
    )
    body = (
        "<rss version='2.0'><channel><title>Publisher</title>" + entries + "</channel></rss>"
    ).encode()
    inferred = []

    def infer(parsed):
        inferred.append(len(parsed.entries))
        return replace(parsed, entries=[replace(entry, language="en") for entry in parsed.entries])

    monkeypatch.setattr(tasks, "detect_feed_languages", infer)
    monkeypatch.setattr(tasks, "fetch_feed", lambda url, *_: FetchResult(200, body, url))
    source_id, job_id = make_job(database)
    tasks.ingest(str(job_id))
    with database.begin() as session:
        repeat = request_ingestion(session, session.get(Source, source_id)).id
    origin_reads = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if "article_origins.entry_key" in statement:
            origin_reads.append(statement)

    event.listen(get_engine(), "before_cursor_execute", observe)
    try:
        tasks.ingest(str(repeat))
    finally:
        event.remove(get_engine(), "before_cursor_execute", observe)
    assert inferred == [500, 500]
    assert len(origin_reads) == 1
    assert all(" IN (" in statement for statement in origin_reads)
    assert all("ingestion_jobs" not in statement for statement in origin_reads)
    assert all("FOR UPDATE" not in statement for statement in origin_reads)
    with database() as session:
        job = session.get(IngestionJob, repeat)
        assert (job.status, job.entries_seen, job.new_source_entries, job.articles_created) == (
            "succeeded",
            500,
            0,
            0,
        )
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 500


def test_origin_removed_during_inference_uses_current_batched_snapshot(database, monkeypatch):
    from dataclasses import replace

    from sqlalchemy import delete

    counts = []
    items = "".join(
        f"<item><guid>retained-{index}</guid><title>Engineering article {index}</title>"
        f"<link>https://example.com/retained-{index}</link>"
        "<description>A useful publisher summary.</description></item>"
        for index in range(128)
    )
    body = (
        "<rss version='2.0'><channel><title>Publisher</title>" + items + "</channel></rss>"
    ).encode()
    source_id, job_id = make_job(database)
    removed = []

    def infer(parsed):
        counts.append(len(parsed.entries))
        if len(counts) == 2:
            # Inference holds no SQL connection. This opposing writer removes
            # one origin before the authoritative persistence snapshot is read.
            with database.begin() as session:
                identifier = session.scalar(select(Article.id).order_by(Article.id))
                removed.append(identifier)
                session.execute(delete(Article).where(Article.id == identifier))
        language = "ja" if len(counts) > 1 else "en"
        return replace(
            parsed, entries=[replace(entry, language=language) for entry in parsed.entries]
        )

    monkeypatch.setattr(tasks, "detect_feed_languages", infer)
    monkeypatch.setattr(tasks, "fetch_feed", lambda url, *_: FetchResult(200, body, url))
    tasks.ingest(str(job_id))
    new_body = body.replace(
        b"</channel>",
        b"<item><guid>new-entry</guid><title>Another engineering article</title>"
        b"<link>https://example.com/new-entry</link><description>A publisher summary.</description>"
        b"</item></channel>",
    )
    with database.begin() as session:
        repeat = request_ingestion(session, session.get(Source, source_id)).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda url, *_: FetchResult(200, new_body, url))
    tasks.ingest(str(repeat))
    assert counts == [128, 129]
    with database() as session:
        job = session.get(IngestionJob, repeat)
        assert job.status == "succeeded" and job.attempts == 1 and job.articles_created == 2
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 129
        assert session.get(Article, removed[0]) is None
        assert (
            session.scalar(
                select(func.count()).select_from(Article).where(Article.language == "ja")
            )
            == 2
        )


@pytest.mark.parametrize("count", [0, 1])
def test_tiny_feeds_keep_the_existing_origin_query_and_transaction_budget(
    database, monkeypatch, count
):
    from devfeed_core.db import get_engine
    from sqlalchemy import event

    items = "".join(
        f"<item><guid>tiny-{index}</guid><title>Engineering article {index}</title>"
        f"<link>https://example.com/tiny-{index}</link>"
        "<description>A useful publisher summary.</description></item>"
        for index in range(count)
    )
    body = (
        "<rss version='2.0'><channel><title>Publisher</title>" + items + "</channel></rss>"
    ).encode()
    monkeypatch.setattr(tasks, "fetch_feed", lambda url, *_: FetchResult(200, body, url))
    source_id, identifier = make_job(database)
    tasks.ingest(str(identifier))
    with database.begin() as session:
        repeat = request_ingestion(session, session.get(Source, source_id)).id
    origin_reads, transactions = [], []

    def statement(_connection, _cursor, sql, *_):
        if "article_origins.entry_key" in sql:
            origin_reads.append(sql)

    def begin(_connection):
        transactions.append(1)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "begin", begin)
    try:
        tasks.ingest(str(repeat))
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "begin", begin)
    assert len(transactions) == 2  # Existing claim and persistence transactions only.
    assert len(origin_reads) == count
    assert all("ingestion_jobs" not in sql and " IN (" not in sql for sql in origin_reads)


def test_batched_origin_lookup_uses_index_with_large_retained_source_history(database, rss_bytes):
    import json

    from devfeed_core.feeds.parser import parse_feed
    from sqlalchemy import text

    source_id, _ = make_job(database)
    with database.begin() as session:
        article = Article(
            title="Retained article",
            summary="A sufficiently useful publisher summary.",
            canonical_url="https://example.com/retained",
            url_hash="a" * 64,
        )
        session.add(article)
        session.flush()
        # Query selectivity depends on the retained origin history, independent
        # of how many old GUIDs refer to the same canonical article.
        # Seed only this disposable history without queueing 100,000 downstream
        # events; internal foreign-key triggers remain active.
        session.execute(text("ALTER TABLE article_origins DISABLE TRIGGER USER"))
        session.execute(
            text(
                "INSERT INTO article_origins(id,article_id,source_id,entry_key,original_url,"
                "source_metadata) SELECT md5('origin-plan-'||g::text)::uuid,:article,:source,"
                "'historical-'||g,'https://example.com/retained','{}' "
                "FROM generate_series(1,100000) g"
            ),
            {"article": article.id, "source": source_id},
        )
        session.execute(text("ALTER TABLE article_origins ENABLE TRIGGER USER"))
        session.execute(text("ANALYZE article_origins"))
    parsed = parse_feed(rss_bytes, "https://example.com/rss", utcnow(), source_type="publisher")
    keys = [f"historical-{index * 199}" for index in range(1, 501)]
    parsed = replace(parsed, entries=[replace(parsed.entries[0], entry_key=key) for key in keys])
    query = select(ArticleOrigin.entry_key, ArticleOrigin.article_id).where(
        ArticleOrigin.source_id == source_id, ArticleOrigin.entry_key.in_(keys)
    )
    with database() as session:
        assert set(tasks._entry_origins(session, source_id, parsed)) == set(keys)
        sql = str(query.compile(bind=session.get_bind(), compile_kwargs={"literal_binds": True}))
        report = session.scalar(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"))[0]
    nodes = []

    def visit(node):
        nodes.append(node)
        for child in node.get("Plans", []):
            visit(child)

    visit(report["Plan"])
    assert report["Plan"]["Actual Rows"] == 500
    assert any("Index" in node["Node Type"] for node in nodes)
    assert not any(
        node["Node Type"] == "Seq Scan" and node.get("Relation Name") == "article_origins"
        for node in nodes
    )
    print(
        json.dumps(
            {
                "source_origin_history": 100000,
                "feed_entries": 500,
                "plan": report,
            }
        )
    )
