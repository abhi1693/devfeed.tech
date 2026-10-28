"""Throughput changes preserve evidence, fairness and worker admission boundaries."""

import threading
from datetime import timedelta

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import utcnow
from devfeed_core.pipeline_capacity import topic_admission_limit, worker_capacity
from devfeed_core.topic_decisions import fetch_bundle, page_snapshot

from tests.analysis.test_research_evidence import evidence_cache as evidence_cache


def test_evidence_fetches_overlap_and_keep_source_order(monkeypatch):
    from devfeed_core import topic_decisions

    monkeypatch.setattr(topic_decisions, "validate_public_url", lambda _: None)
    barrier = threading.Barrier(3)
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        assert 0 < timeout <= get_settings().evidence_timeout_seconds
        barrier.wait(timeout=2)  # Sequential fetching cannot pass this barrier.
        return {"text": url + " supports this topic.", "final_url": url, "content_hash": url}

    urls = [f"https://example.com/{i}" for i in range(3)]
    result = fetch_bundle(urls, fetch=fetch)
    assert sorted(seen) == urls
    assert [p["url"] for p in result["pages"]] == urls
    assert [p["sentences"][0]["id"] for p in result["pages"]] == ["p1s1", "p2s1", "p3s1"]


def test_duplicate_evidence_is_fetched_once_and_bad_url_is_not_fetched(monkeypatch):
    from devfeed_core.feeds.fetcher import FeedError

    calls = []

    def fetch(url, timeout):
        calls.append(url)
        raise FeedError("gone", reason="http_error", retryable=False)

    result = fetch_bundle(
        ["http://127.0.0.1/", "https://example.com/", "https://example.com/"], fetch=fetch
    )
    assert calls == ["https://example.com/"]
    assert result["pages"] == [] and len(result["failures"]) == 2


@pytest.mark.parametrize("reusable", [True, False])
def test_topic_evidence_reuse_preserves_age_and_never_serves_expired(
    monkeypatch, evidence_cache, reusable
):
    from devfeed_core import topic_evidence
    from devfeed_core.feeds.fetcher import FeedError

    calls = []
    now = utcnow()
    page = {
        "text": "Exact source evidence",
        "final_url": "https://example.com/",
        "content_hash": "hash",
        "validated_at": now.isoformat(),
        "reusable": reusable,
    }

    def fetch(url, timeout):
        calls.append(url)
        return page.copy()

    monkeypatch.setattr(topic_evidence, "fetched_page", fetch)
    first = topic_evidence.reusable_page("https://example.com/", 10)
    second = topic_evidence.reusable_page("https://example.com/", 10)
    assert len(calls) == (1 if reusable else 2)
    assert page_snapshot("https://example.com/", second)["fetched_at"] == first["validated_at"]
    monkeypatch.setattr(topic_evidence, "utcnow", lambda: now + timedelta(seconds=301))

    def fail(*_):
        raise FeedError("offline", reason="transport_error", retryable=True)

    monkeypatch.setattr(topic_evidence, "fetched_page", fail)
    with pytest.raises(FeedError):
        topic_evidence.reusable_page("https://example.com/", 10)


@pytest.mark.parametrize(
    "capacity,expected",
    [
        ({"observed": False}, 4),
        ({"observed": True, "cooldown_seconds": 10, "topic_workers": 3}, 0),
        ({"observed": True, "cooldown_seconds": 0, "topic_workers": 0}, 0),
        ({"observed": True, "cooldown_seconds": 0, "topic_workers": 3}, 6),
        ({"observed": True, "cooldown_seconds": 0, "topic_workers": 100}, 8),
    ],
)
def test_admission_follows_observed_workers_and_hard_cap(capacity, expected):
    assert topic_admission_limit(capacity) == expected


@pytest.mark.integration
def test_capacity_excludes_expired_heartbeats_and_busy_shared_workers(database):
    from devfeed_core.redis import create_redis

    redis = create_redis(get_settings())
    for name, queues, state, heartbeat in [
        ("topic", "topic-analysis", "busy", utcnow()),
        ("mixed", "topic-analysis,article-analysis", "busy", utcnow()),
        ("stale", "topic-analysis", "idle", utcnow() - timedelta(minutes=3)),
        ("article", "article-analysis", "idle", utcnow()),
    ]:
        key = f"rq:worker:{name}"
        redis.sadd("rq:workers", key)
        redis.hset(
            key, mapping={"queues": queues, "state": state, "last_heartbeat": heartbeat.isoformat()}
        )
        redis.expire(key, 300)
    result = worker_capacity(redis)
    assert result["topic_workers"] == 1 and result["idle_topic_workers"] == 0
    assert result["article_workers"] == 1 and result["idle_article_workers"] == 1
    assert result["shared_workers"] == 1


@pytest.mark.integration
def test_priority_reserves_oldest_slot_and_uses_only_eligible_article_demand(database, monkeypatch):
    import uuid
    from datetime import UTC, datetime

    from devfeed_core.models import Article, ArticleTag, Tag, TopicAnalysisJob, TopicProposal
    from devfeed_core.topic_decision_budget import schedule_decisions
    from sqlalchemy import select

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    monkeypatch.setattr(settings, "ai_bounded_topics_enabled", True)
    monkeypatch.setattr(settings, "ai_content_not_before", datetime(2026, 7, 1).date())
    identifiers = {}
    with database.begin() as session:
        for i, (slug, count, published) in enumerate(
            [
                ("oldest", 0, None),
                ("low", 1, datetime(2026, 8, 1, tzinfo=UTC)),
                ("high", 5, datetime(2026, 8, 1, tzinfo=UTC)),
                ("excluded", 10, datetime(2026, 6, 1, tzinfo=UTC)),
            ]
        ):
            proposal = TopicProposal(
                batch_id=uuid.uuid4(),
                slug=slug,
                action="create",
                origin="import",
                source_name="test",
                proposed={"name": slug, "slug": slug},
                created_by={},
                created_at=utcnow() - timedelta(days=10 - i),
            )
            tag = Tag(name=slug, slug=slug)
            session.add_all([proposal, tag])
            session.flush()
            identifiers[slug] = proposal.id
            for j in range(count):
                article = Article(
                    title=slug,
                    canonical_url=f"https://example.com/{slug}/{j}",
                    url_hash=f"{slug}-{j}",
                    published_at=published,
                )
                session.add(article)
                session.flush()
                session.add(ArticleTag(article_id=article.id, tag_id=tag.id))
    capacity = {"observed": True, "cooldown_seconds": 0, "topic_workers": 1}
    assert schedule_decisions(database, capacity=capacity) == 2
    with database() as session:
        assert set(session.scalars(select(TopicAnalysisJob.proposal_id))) == {
            identifiers["oldest"],
            identifiers["high"],
        }
        assert all(p.status == "pending" for p in session.scalars(select(TopicProposal)))
    assert schedule_decisions(database, capacity=capacity) == 0  # No duplicates or cap overflow.


@pytest.mark.integration
def test_throughput_does_not_count_deferred_analyses_as_useful_completion(database):
    from devfeed_core.models import Article, ArticleAnalysisJob
    from sqlalchemy import text

    from tests.support import analytics

    now = utcnow()
    with database.begin() as session:
        article = Article(
            title="Published",
            canonical_url="https://example.com/p",
            url_hash="pub",
            published_to_feed_at=now - timedelta(minutes=1),
        )
        session.add(article)
        session.flush()
        for outcome in ("applied", "content_date_deferred"):
            session.add(
                ArticleAnalysisJob(
                    article_id=article.id,
                    input_hash=outcome,
                    input_snapshot={},
                    editorial_revision=0,
                    status="succeeded",
                    outcome=outcome,
                    finished_at=now - timedelta(minutes=1),
                    duration_ms=1000,
                )
            )
    with database() as session:
        session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
        result = analytics.load_section(session, "processing", 1, now).data
    assert sum(bucket.articles_published for bucket in result.throughput) == 1
    assert sum(bucket.article_analyses for bucket in result.throughput) == 1
    assert sum(bucket.topic_decisions for bucket in result.throughput) == 0
    assert len(result.throughput) == now.hour + 1
    analyses = next(row for row in result.outcomes if row.kind == "analysis")
    assert analyses.completed == 2 and analyses.failed == 0


def test_capacity_reports_busy_shared_workers_without_changing_admission(monkeypatch):
    from devfeed_core import pipeline_capacity

    monkeypatch.setattr(pipeline_capacity, "cooldown_remaining", lambda _: 0)
    now = utcnow()
    rows = [
        (["topic-analysis", "busy", None, now.isoformat()], 300),
        (["topic-analysis,article-analysis", "busy", None, now.isoformat()], 300),
        (["article-analysis", "idle", None, now.isoformat()], 300),
        (["article-analysis", "idle", None, (now - timedelta(minutes=3)).isoformat()], 300),
    ]

    class Registry:
        def smembers(self, _):
            return {str(i) for i in range(len(rows))}

        def pipeline(self, **_):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def hmget(self, *_):
            pass

        def ttl(self, *_):
            pass

        def execute(self):
            return [value for row in rows for value in row]

    result = worker_capacity(Registry())
    assert result["eligible_article_workers"] == 2
    assert result["eligible_topic_workers"] == 2
    assert result["busy_article_workers"] == 1
    assert result["busy_topic_workers"] == 2
    assert result["idle_article_workers"] == 1
    assert result["idle_topic_workers"] == 0
    assert result["shared_workers"] == 1
    # The scheduler still excludes a busy mixed worker from reservable capacity.
    assert result["article_workers"] == result["topic_workers"] == 1


@pytest.mark.parametrize(
    "queues,state,topic_slots,article_slots",
    [
        ("topic-analysis", "busy", 1, 0),
        ("article-analysis-fresh,article-analysis", "busy", 0, 1),
        ("topic-analysis,article-analysis", "busy", 0, 0),
        ("topic-analysis,article-analysis", "idle", 1, 1),
        ("topic-analysis,ingestion", "busy", 0, 0),
    ],
)
def test_capacity_reserves_busy_slots_only_within_one_execution_family(
    monkeypatch, queues, state, topic_slots, article_slots
):
    from unittest.mock import MagicMock

    from devfeed_core import pipeline_capacity

    now = utcnow()
    connection = MagicMock()
    connection.smembers.return_value = {b"rq:worker:pool"}
    connection.pipeline.return_value.__enter__.return_value.execute.return_value = [
        [queues.encode(), state.encode(), b"", now.isoformat().encode()],
        90,
    ]
    monkeypatch.setattr(pipeline_capacity, "utcnow", lambda: now)
    monkeypatch.setattr(pipeline_capacity, "cooldown_remaining", lambda _: 0)
    capacity = worker_capacity(connection)
    assert capacity["topic_workers"] == topic_slots
    assert capacity["article_workers"] == article_slots
    assert capacity["idle_topic_workers"] == (topic_slots if state == "idle" else 0)
    assert capacity["idle_article_workers"] == (article_slots if state == "idle" else 0)


@pytest.fixture
def priority_transport(database, monkeypatch):
    from devfeed_aggregator.analysis_worker import AdmissionSnapshot, AnalysisAwareWorker
    from devfeed_aggregator.queue import get_queue

    monkeypatch.setattr(get_settings(), "job_queue_mode", "priority")
    queue = get_queue("default", physical=True)
    worker = AnalysisAwareWorker(
        [queue], connection=queue.connection, serializer=queue.serializer, prepare_for_work=False
    )
    worker.admission_snapshot = lambda: AdmissionSnapshot({"default": True})
    yield worker, queue, get_queue
    queue.connection.close()


@pytest.mark.integration
def test_priority_scan_claims_io_behind_paused_ai_without_moving_paused_deliveries(
    priority_transport, monkeypatch
):
    from devfeed_aggregator.analysis_worker import AdmissionSnapshot

    worker, physical, get_queue = priority_transport
    monkeypatch.setattr(worker.codex_readiness, "ready", lambda **kwargs: False)
    ai = get_queue("article-analysis")
    io = get_queue("ingestion")
    try:
        pending = [ai.enqueue("builtins.str", str(index)).id for index in range(150)]
        selected = io.enqueue("builtins.str", "ready")
        snapshot = AdmissionSnapshot({"default": True})
        assert worker.dequeue_priority(snapshot) is None
        job, queue = worker.dequeue_priority(snapshot)
        assert job.id == selected.id and queue.name == "default"
        assert physical.job_ids == pending
        assert physical.intermediate_queue.get_job_ids() == [selected.id]
        # A racing/stale claim cannot remove or duplicate the already claimed delivery.
        from devfeed_aggregator.analysis_worker import CLAIM_DELIVERY

        assert (
            physical.connection.eval(
                CLAIM_DELIVERY,
                3,
                physical.key,
                physical.intermediate_queue.key,
                selected.key,
                selected.id,
                physical.name,
            )
            == 0
        )
        assert physical.intermediate_queue.get_job_ids() == [selected.id]
    finally:
        ai.connection.close()
        io.connection.close()


@pytest.mark.integration
def test_priority_claim_retains_rq_recovery_when_worker_dies_before_execution(priority_transport):
    from devfeed_aggregator.analysis_worker import AdmissionSnapshot
    from rq.job import JobStatus

    worker, physical, get_queue = priority_transport
    source = get_queue("ingestion")
    try:
        delivery = source.enqueue("builtins.str", "ready")
        assert worker.dequeue_priority(AdmissionSnapshot({"default": True}))[0].id == delivery.id
        intermediate = physical.intermediate_queue
        # RQ observes an unstarted intermediate delivery, then detects it stale.
        intermediate.cleanup(worker, physical)
        physical.connection.set(
            intermediate.get_first_seen_key(delivery.id),
            (utcnow() - timedelta(minutes=2)).timestamp(),
            ex=3600,
        )
        intermediate.cleanup(worker, physical)
        assert delivery.get_status(refresh=True) == JobStatus.FAILED
        assert physical.failed_job_registry.get_job_ids() == [delivery.id]
        assert intermediate.get_job_ids() == []
    finally:
        source.connection.close()


@pytest.mark.integration
@pytest.mark.parametrize("state", ["busy", "idle"])
def test_priority_capacity_observes_fresh_article_slots_and_admits_bounded_topic_work(
    database, monkeypatch, state
):
    from devfeed_core.redis import create_redis

    monkeypatch.setattr(get_settings(), "job_queue_mode", "priority")
    with create_redis(get_settings()) as redis:
        for name, queue in [
            ("high-1", "high"),
            ("high-2", "high"),
            ("default-1", "default"),
            ("default-2", "default"),
            ("low", "low"),
        ]:
            key = f"rq:worker:{name}"
            redis.sadd("rq:workers", key)
            redis.hset(
                key,
                mapping={"queues": queue, "state": state, "last_heartbeat": utcnow().isoformat()},
            )
            redis.expire(key, 90)
        capacity = worker_capacity(redis)
        assert capacity["eligible_article_workers"] == 4
        assert capacity["eligible_topic_workers"] == 2
        assert topic_admission_limit(capacity) == 4


@pytest.mark.parametrize("dedicated_override,expected", [(False, 0), (True, 1)])
def test_priority_capacity_ignores_drain_only_named_consumer(
    monkeypatch, dedicated_override, expected
):
    from unittest.mock import MagicMock

    from devfeed_core import pipeline_capacity

    settings = get_settings()
    monkeypatch.setattr(settings, "job_queue_mode", "priority")
    monkeypatch.setattr(
        settings,
        "job_queue_overrides",
        {"topic-analysis": "topic-analysis"} if dedicated_override else {},
    )
    now = utcnow()
    connection = MagicMock()
    connection.smembers.return_value = {b"rq:worker:old-topic"}
    connection.pipeline.return_value.__enter__.return_value.execute.return_value = [
        [b"topic-analysis", b"idle", b"", now.isoformat().encode()],
        90,
    ]
    monkeypatch.setattr(pipeline_capacity, "utcnow", lambda: now)
    monkeypatch.setattr(pipeline_capacity, "cooldown_remaining", lambda _: 0)
    observed = worker_capacity(connection)
    assert observed["eligible_topic_workers"] == expected
    assert topic_admission_limit(observed) == expected * settings.topic_decision_worker_buffer
