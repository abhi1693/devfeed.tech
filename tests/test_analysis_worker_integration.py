"""Readiness gating uses the real Redis queues and preserves durable job attempts."""

import uuid
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_worker
from devfeed_aggregator.analysis_worker import AnalysisAwareWorker
from devfeed_aggregator.queue import get_queue
from devfeed_core.models import Article, ArticleAnalysisJob, TopicAnalysisJob, TopicProposal
from rq.serializers import JSONSerializer
from rq.worker import DequeueStrategy, WorkerStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def waiting(database):
    with database.begin() as session:
        article = Article(
            title="Waiting article", canonical_url="https://example.com/", url_hash="test"
        )
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="waiting",
            action="create",
            origin="import",
            source_name="Test",
            proposed={"name": "Waiting", "slug": "waiting"},
            created_by={},
        )
        session.add_all([article, proposal])
        session.flush()
        jobs = [
            ArticleAnalysisJob(article_id=article.id),
            TopicAnalysisJob(
                proposal_id=proposal.id,
                input_hash="a" * 64,
                input_snapshot={},
                requested_by={},
                prompt_version="test",
            ),
        ]
        session.add_all(jobs)
        session.flush()
        identifiers = [job.id for job in jobs]
    queue = get_queue("analysis")
    messages = [
        queue.enqueue(function, str(identifier))
        for function, identifier in zip(
            [
                "devfeed_aggregator.analysis_tasks.analyze_article",
                "devfeed_aggregator.topic_analysis_tasks.analyze_topic",
            ],
            identifiers,
            strict=True,
        )
    ]
    yield queue, messages, identifiers
    queue.connection.close()


def consumer(queues):
    result = AnalysisAwareWorker(
        queues,
        connection=queues[0].connection,
        serializer=JSONSerializer,
        name="readiness-" + uuid.uuid4().hex,
    )
    result._dequeue_strategy = DequeueStrategy.ROUND_ROBIN
    return result


def test_outage_keeps_both_job_types_queued_with_heartbeats_then_resumes(
    database, waiting, monkeypatch, caplog
):
    queue, messages, identifiers = waiting
    instance = consumer([queue])
    clock, checks, sleeps = [0.0], [], []

    def check():
        checks.append(clock[0])
        return "codex_unavailable" if clock[0] < 20 else None

    def sleep(seconds):
        sleeps.append(seconds)
        assert queue.job_ids == [job.id for job in messages]
        assert instance.get_state() == WorkerStatus.SUSPENDED
        assert queue.connection.ttl(instance.key) > 0
        assert queue.connection.hget(instance.key, "last_heartbeat")
        with database() as session:
            for model, identifier in zip(
                [ArticleAnalysisJob, TopicAnalysisJob], identifiers, strict=True
            ):
                job = session.get(model, identifier)
                assert job.status == "queued" and job.attempts == 0 and job.lease_token is None
        clock[0] += seconds

    monkeypatch.setattr(
        analysis_worker, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    instance.codex_readiness.client = SimpleNamespace(readiness=check)
    instance.register_birth()
    try:
        with caplog.at_level("INFO", logger=analysis_worker.__name__):
            job, selected = instance.dequeue_job_and_maintain_ttl(timeout=10)
        assert job.id == messages[0].id and selected.name == "analysis"
        assert queue.job_ids == [messages[1].id]
        assert checks == [0, 10, 20] and len(sleeps) == 20
        assert instance.get_state() == WorkerStatus.IDLE
        assert [
            record.message for record in caplog.records if record.name == analysis_worker.__name__
        ] == ["analysis_queue_paused", "analysis_queue_resumed"]
        assert instance.dequeue_job_and_maintain_ttl(timeout=None)[0].id == messages[1].id
        assert checks == [0, 10, 20, 20]  # Recheck before each subsequent message too.
    finally:
        instance.register_death()


def test_mixed_worker_drains_background_queues_while_analysis_is_paused(database, waiting):
    queue, messages, _ = waiting
    ingestion, notifications = get_queue(), get_queue("notifications")
    first = ingestion.enqueue("builtins.str", "ingest")
    second = notifications.enqueue("builtins.str", "notify")
    instance = consumer([ingestion, queue, notifications])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    try:
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == first.id
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == second.id
        assert instance.dequeue_job_and_maintain_ttl(None) is None
        assert queue.job_ids == [job.id for job in messages]
        instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == messages[0].id
        assert {item.name for item in instance._ordered_queues} == {
            "analysis",
            "ingestion",
            "notifications",
        }
    finally:
        ingestion.connection.close()
        notifications.connection.close()


def test_jobs_arriving_after_idle_outage_cannot_use_cached_healthy_readiness(database, monkeypatch):
    queue = get_queue("analysis")
    instance = consumer([queue])
    clock, messages, checks = [0.0], [], []

    def check():
        checks.append(clock[0])
        return None if clock[0] == 0 else "codex_unavailable"

    def sleep(seconds):
        if not messages:
            messages.append(queue.enqueue("builtins.str", "must stay queued"))
        clock[0] += seconds

    monkeypatch.setattr(
        analysis_worker, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    instance.codex_readiness.client = SimpleNamespace(readiness=check)
    try:
        assert instance.dequeue_job_and_maintain_ttl(10, max_idle_time=3) is None
        assert checks == [0, 1]
        assert queue.job_ids == [job.id for job in messages]
    finally:
        queue.connection.close()


def test_paused_burst_worker_exits_cleanly_without_processing_messages(database, waiting):
    queue, messages, _ = waiting
    instance = consumer([queue])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    assert instance.work(burst=True) is False
    assert queue.job_ids == [job.id for job in messages]


def test_paused_worker_honors_stop_request(database, waiting, monkeypatch):
    queue, messages, _ = waiting
    instance = consumer([queue])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    monkeypatch.setattr(
        analysis_worker,
        "time",
        SimpleNamespace(
            monotonic=lambda: 0, sleep=lambda _: setattr(instance, "_stop_requested", True)
        ),
    )
    assert instance.dequeue_job_and_maintain_ttl(10) is None
    assert queue.job_ids == [job.id for job in messages]


def test_background_only_worker_does_not_probe_codex(database):
    queue = get_queue()
    message = queue.enqueue("builtins.str", "ingest")
    instance = consumer([queue])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: pytest.fail("Probed Codex"))
    try:
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == message.id
    finally:
        queue.connection.close()
