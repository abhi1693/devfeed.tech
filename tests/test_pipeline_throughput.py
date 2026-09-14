"""Throughput changes preserve evidence, fairness and worker admission boundaries."""

import threading
from datetime import timedelta

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import utcnow
from devfeed_core.pipeline_capacity import topic_admission_limit, worker_capacity
from devfeed_core.topic_decisions import fetch_bundle, page_snapshot
from test_research_evidence import evidence_cache as evidence_cache


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
def test_throughput_does_not_count_deferred_analyses_as_useful_completion(database, monkeypatch):
    from devfeed_core import pipeline_metrics
    from devfeed_core.models import Article, ArticleAnalysisJob
    from sqlalchemy import text

    now = utcnow()
    monkeypatch.setattr(pipeline_metrics, "observe_capacity", lambda: {"observed": False})
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
        result = pipeline_metrics.pipeline_throughput(session, now)
    assert sum(h.articles_published for h in result.hours) == 1
    assert sum(h.article_analyses for h in result.hours) == 1
    assert sum(h.topic_decisions for h in result.hours) == 0
    assert len(result.hours) == 24
    assert not result.capacity_observed and result.topic_workers is None
    assert result.queues[0].completed == 2  # Transport completion is separately labelled.


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
