"""Regressions for pool starvation, recommendation deadlocks and worker health."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_core.analysis import Classifications, replace_classifications
from devfeed_core.models import Article, ArticleTopic, RecommendationTopicEvent, Topic, utcnow
from devfeed_core.transaction_retry import run_transaction
from devfeed_core.worker_health import healthy
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from test_automation_integration import seed


def test_worker_probe_requires_live_parent_and_recent_heartbeat(tmp_path):
    path = tmp_path / "health.json"
    assert not healthy(str(path))
    record = {"pid": os.getpid(), "at": time.monotonic(), "ttl": 90}
    path.write_text(json.dumps(record))
    assert healthy(str(path))
    path.write_text(json.dumps({**record, "at": time.monotonic() - 100}))
    assert not healthy(str(path))
    path.write_text(json.dumps({**record, "pid": 99999999}))
    assert not healthy(str(path))
    path.write_text("partial")
    assert not healthy(str(path))


@pytest.mark.integration
@pytest.mark.parametrize("existing_assignments", [False, True])
def test_classification_replacement_serializes_overlapping_event_rows(
    database, existing_assignments
):
    with database.begin() as session:
        articles = [seed(session, topic=False)[1].id for _ in range(2)]
        topics = [
            Topic(
                name=f"Technology {i}", slug=f"technology-{i}", kind="technology", status="active"
            )
            for i in range(4)
        ]
        session.add_all(topics)
        session.flush()
        topic_ids = sorted(t.id for t in topics)
        # Include old topics which will be deleted, retained manual topics and
        # newly assigned topics. Event rows already exist for only part of the set.
        for article_id in articles if existing_assignments else []:
            session.add_all(
                [
                    ArticleTopic(
                        article_id=article_id,
                        topic_id=topic_ids[0],
                        role="primary",
                        relevance=1,
                        evidence="Technology",
                        origin="ai",
                    ),
                    ArticleTopic(
                        article_id=article_id,
                        topic_id=topic_ids[1],
                        role="supporting",
                        relevance=1,
                        evidence="Technology",
                        origin="manual",
                    ),
                ]
            )
    barrier = threading.Barrier(2)

    def replace(index):
        with database.begin() as session:
            session.execute(text("SET LOCAL lock_timeout='4s'"))
            article = session.get(Article, articles[index])
            ids = topic_ids[1:] if index == 0 else list(reversed(topic_ids[1:]))
            result = Classifications(
                developer_relevance="relevant",
                language="en",
                content_type="article",
                content_format="article",
                tags=[],
                topics=[
                    dict(topic_id=id_, role="supporting", relevance=1, evidence="Technology")
                    for id_ in ids
                ],
            )
            barrier.wait(timeout=5)
            replace_classifications(session, article, result, origin="ai")
            session.flush()
            # Keep all trigger locks through the final publication update.
            article.review_status = "approved"
            article.publication_status = "published"
            session.flush()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(replace, index) for index in range(2)]
        for future in futures:
            future.result(timeout=10)
    with database() as session:
        for id_ in articles:
            rows = session.scalars(select(ArticleTopic).where(ArticleTopic.article_id == id_)).all()
            assert {r.topic_id for r in rows} == set(topic_ids[1:])
            assert next(r for r in rows if r.topic_id == topic_ids[1]).origin == (
                "manual" if existing_assignments else "ai"
            )
        assert all(r.version > 0 for r in session.scalars(select(RecommendationTopicEvent)))


@pytest.mark.integration
def test_transaction_retry_rolls_back_and_does_not_repeat_external_work(database, monkeypatch):
    monkeypatch.setattr("devfeed_core.transaction_retry.time.sleep", lambda _: None)
    with database.begin() as session:
        _, article, _ = seed(session, topic=False)
        identifier = article.id
    attempts = []

    class Deadlock(Exception):
        sqlstate = "40P01"

    def apply(session):
        article = session.get(Article, identifier)
        attempts.append(article.editorial_revision)
        article.editorial_revision += 1
        session.flush()
        if len(attempts) < 3:
            raise OperationalError("redacted", {}, Deadlock())
        return article.editorial_revision

    assert run_transaction(database, apply) == 1
    assert attempts == [0, 0, 0]
    with database() as session:
        assert session.get(Article, identifier).editorial_revision == 1


@pytest.mark.integration
def test_idle_worker_threshold_matches_rq_but_busy_stalls_are_detected(database):
    from devfeed_core.config import get_settings
    from devfeed_core.observability_exporter import redis_snapshot
    from redis import Redis

    now = utcnow()
    with Redis.from_url(get_settings().redis_url) as redis:
        for name, state, age, ttl in (
            ("legacy", "idle", 400, 420),
            ("current", "idle", 100, 90),
            ("stuck", "busy", 100, 420),
        ):
            key = "rq:worker:" + name
            redis.sadd("rq:workers", key)
            redis.hset(
                key,
                mapping={
                    "queues": "images",
                    "state": state,
                    "worker_ttl": ttl,
                    "last_heartbeat": (now - timedelta(seconds=age)).isoformat(),
                },
            )
            redis.expire(key, 120)
        samples = [s for f in redis_snapshot(redis, now) for s in f.samples]
        assert (
            next(
                s.value
                for s in samples
                if s.name.endswith("queue_workers")
                and s.labels == {"queue": "images", "state": "idle"}
            )
            == 2
        )
        assert (
            next(
                s.value
                for s in samples
                if s.name.endswith("queue_stale_workers") and s.labels == {"queue": "images"}
            )
            == 1
        )


@pytest.mark.integration
def test_public_cache_publication_does_not_hold_database_connection(database, monkeypatch):
    from devfeed_api import cache, dependencies
    from devfeed_core.config import get_settings
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    published = []
    engine = database.kw["bind"]
    connections = {"active": 0, "total": 0}

    def checkout(*args):
        connections["active"] += 1
        connections["total"] += 1

    def checkin(*args):
        connections["active"] -= 1

    # Observe actual checkout/return, independent of QueuePool-only introspection.
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)

    def publish(*args):
        assert connections == {"active": 0, "total": 1}
        published.append(True)

    monkeypatch.setattr(
        cache,
        "get_cache",
        lambda: SimpleNamespace(
            lookup=lambda *a: SimpleNamespace(body=None, token="owner"),
            publish=publish,
            release=lambda *a: None,
        ),
    )
    app = FastAPI()
    router = APIRouter(route_class=cache.CachedReadRoute)

    monkeypatch.setattr(dependencies, "session_factory", lambda: database)

    @router.get("/test")
    def read(session: dependencies.DB):
        return {"value": session.scalar(text("SELECT 1"))}

    app.include_router(router)
    try:
        with TestClient(app) as client:
            assert client.get("/test").json() == {"value": 1}
        assert published == [True]
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)


@pytest.mark.integration
def test_parent_heartbeat_updates_local_probe_and_registration(database, monkeypatch, tmp_path):
    from devfeed_aggregator.analysis_worker import AnalysisAwareWorker
    from devfeed_core.config import get_settings
    from redis import Redis
    from rq import Queue
    from rq.worker import WorkerStatus

    path = tmp_path / "worker.json"
    monkeypatch.setenv("DEVFEED_WORKER_HEALTH_PATH", str(path))
    with Redis.from_url(get_settings().redis_url) as redis:
        worker = AnalysisAwareWorker(
            [Queue("images", connection=redis)], connection=redis, worker_ttl=90
        )
        worker.register_birth()
        try:
            worker.set_state(WorkerStatus.IDLE)
            worker.heartbeat()
            assert healthy(str(path))
            assert worker.dequeue_timeout == 75
            assert redis.hget(worker.key, "worker_ttl") == b"90"
            worker.set_state(WorkerStatus.BUSY)
            worker.heartbeat()
            assert json.loads(path.read_text())["ttl"] == 90
        finally:
            worker.register_death()
