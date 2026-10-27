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
    queues = [get_queue("article-analysis"), get_queue("topic-analysis")]
    messages = [
        queue.enqueue(function, str(identifier))
        for queue, (function, identifier) in zip(
            queues,
            [
                ("devfeed_aggregator.analysis_tasks.analyze_article", identifiers[0]),
                ("devfeed_aggregator.topic_analysis_tasks.analyze_topic", identifiers[1]),
            ],
            strict=True,
        )
    ]
    yield queues, messages, identifiers
    for queue in queues:
        queue.connection.close()


def queued_ids(queues):
    return [identifier for queue in queues for identifier in queue.job_ids]


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
    queues, messages, identifiers = waiting
    instance = consumer(queues)
    clock, checks, sleeps = [0.0], [], []

    def check():
        checks.append(clock[0])
        return "codex_unavailable" if clock[0] < 20 else None

    def sleep(seconds):
        sleeps.append(seconds)
        assert queued_ids(queues) == [job.id for job in messages]
        assert instance.get_state() == WorkerStatus.SUSPENDED
        assert queues[0].connection.ttl(instance.key) > 0
        assert queues[0].connection.hget(instance.key, "last_heartbeat")
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
        assert job.id == messages[0].id and selected.name == "article-analysis"
        assert queued_ids(queues) == [messages[1].id]
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
    queues, messages, _ = waiting
    ingestion, notifications = get_queue(), get_queue("notifications")
    first = ingestion.enqueue("builtins.str", "ingest")
    second = notifications.enqueue("builtins.str", "notify")
    instance = consumer([ingestion, *queues, notifications])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    try:
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == first.id
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == second.id
        assert instance.dequeue_job_and_maintain_ttl(None) is None
        assert queued_ids(queues) == [job.id for job in messages]
        instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == messages[0].id
        assert {item.name for item in instance._ordered_queues} == {
            "article-analysis",
            "topic-analysis",
            "ingestion",
            "notifications",
        }
    finally:
        ingestion.connection.close()
        notifications.connection.close()


def test_jobs_arriving_after_idle_outage_cannot_use_cached_healthy_readiness(database, monkeypatch):
    queue = get_queue("article-analysis")
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
        assert checks == [1]  # Idle workers do not open provider connections.
        assert queue.job_ids == [job.id for job in messages]
    finally:
        queue.connection.close()


def test_paused_burst_worker_exits_cleanly_without_processing_messages(database, waiting):
    queues, messages, _ = waiting
    instance = consumer(queues)
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    assert instance.work(burst=True) is False
    assert queued_ids(queues) == [job.id for job in messages]


def test_paused_worker_honors_stop_request(database, waiting, monkeypatch):
    queues, messages, _ = waiting
    instance = consumer(queues)
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    monkeypatch.setattr(
        analysis_worker,
        "time",
        SimpleNamespace(
            monotonic=lambda: 0, sleep=lambda _: setattr(instance, "_stop_requested", True)
        ),
    )
    assert instance.dequeue_job_and_maintain_ttl(10) is None
    assert queued_ids(queues) == [job.id for job in messages]


def test_background_only_worker_does_not_probe_codex(database):
    queue = get_queue()
    message = queue.enqueue("builtins.str", "ingest")
    instance = consumer([queue])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: pytest.fail("Probed Codex"))
    try:
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == message.id
    finally:
        queue.connection.close()


def test_ai_queues_alternate_and_both_honor_capacity_cooldown(database, monkeypatch):
    from devfeed_core.ai_capacity import cooldown_key

    analysis, relationships = get_queue("article-analysis"), get_queue("relationships")
    for index in range(3):
        analysis.enqueue("builtins.str", f"metadata-{index}")
        relationships.enqueue("builtins.str", f"relationships-{index}")
    instance = consumer([analysis, relationships])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
    analysis.connection.set(cooldown_key(), "capacity", ex=300)
    try:
        assert instance.dequeue_job_and_maintain_ttl(None) is None
        assert analysis.count == relationships.count == 3
        analysis.connection.delete(cooldown_key())
        names = [instance.dequeue_job_and_maintain_ttl(None)[1].name for _ in range(6)]
        assert names == ["article-analysis", "relationships"] * 3
    finally:
        analysis.connection.close()
        relationships.connection.close()


@pytest.mark.parametrize(
    "queue_name",
    [
        "relationships",
        "article-analysis",
        "topic-analysis",
        "research-verification",
        "source-analysis",
    ],
)
def test_dedicated_ai_worker_requires_codex_readiness(database, queue_name):
    queue = get_queue(queue_name)
    message = queue.enqueue("builtins.str", "research")
    instance = consumer([queue])
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: False, reason="codex_unavailable")
    try:
        assert instance.dequeue_job_and_maintain_ttl(None) is None
        assert queue.job_ids == [message.id]
        instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
        assert instance.dequeue_job_and_maintain_ttl(None)[0].id == message.id
    finally:
        queue.connection.close()


def test_dedicated_topic_worker_bypasses_article_backlog(database):
    article, topic = get_queue("article-analysis"), get_queue("topic-analysis")
    try:
        articles = [article.enqueue("builtins.str", str(i)) for i in range(5)]
        research = topic.enqueue("builtins.str", "research")
        instance = consumer([topic])
        instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
        job, selected = instance.dequeue_job_and_maintain_ttl(None)
        assert job.id == research.id and selected.name == "topic-analysis"
        assert instance.dequeue_job_and_maintain_ttl(None) is None
        assert article.job_ids == [item.id for item in articles]
    finally:
        article.connection.close()
        topic.connection.close()


def test_quota_pacing_keeps_jobs_queued_then_resumes_without_spending_attempts(
    database, waiting, monkeypatch
):
    import json
    import time

    from devfeed_core.config import get_settings
    from devfeed_core.quota_pacing import quota_key

    monkeypatch.setenv("DEVFEED_AI_QUOTA_PACING_ENABLED", "true")
    get_settings.cache_clear()
    queues, messages, identifiers = waiting
    instance = consumer(queues)
    instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
    assert instance.dequeue_job_and_maintain_ttl(timeout=None) is None
    assert queued_ids(queues) == [job.id for job in messages]
    now = time.time()
    snapshot = {
        "checked_at": now,
        "day_end": now + 3600,
        "resets_at": now + 86400,
        "provider_allowed": True,
        "used_percent": 10,
        "ceiling_percent": 9,
    }
    queues[0].connection.set(quota_key(), json.dumps(snapshot))
    assert instance.dequeue_job_and_maintain_ttl(timeout=None) is None
    with database() as session:
        assert session.get(ArticleAnalysisJob, identifiers[0]).attempts == 0
    snapshot["ceiling_percent"] = 20
    queues[0].connection.set(quota_key(), json.dumps(snapshot))
    assert instance.dequeue_job_and_maintain_ttl(timeout=None)[0].id == messages[0].id


@pytest.mark.failure_recovery
@pytest.mark.parametrize("function,args", [("os._exit", (1,)), ("time.sleep", (30,))])
def test_workhorse_crash_and_hard_timeout_leave_parent_able_to_run_next_job(
    database, function, args
):
    import os

    from rq.job import JobStatus

    queue = get_queue("article-analysis")
    try:
        failed = queue.enqueue(function, *args, job_timeout=1, failure_ttl=60)
        following = queue.enqueue("os.getpid", result_ttl=60)
        instance = consumer([queue])
        instance.codex_readiness = SimpleNamespace(ready=lambda **kw: True, reason=None)
        parent = os.getpid()
        instance.work(burst=True, max_jobs=2)
        assert failed.get_status() == JobStatus.FAILED
        assert following.get_status() == JobStatus.FINISHED
        assert following.return_value() != parent
        assert os.getpid() == parent and not instance.horse_pid
        instance.refresh()  # The work horse persisted successful counters in Redis.
        assert instance.failed_job_count == instance.successful_job_count == 1
    finally:
        queue.connection.close()


@pytest.mark.failure_recovery
@pytest.mark.parametrize("queued_response", [1, 2])
def test_sigterm_during_redis_transaction_finishes_protocol_before_worker_teardown(
    database, monkeypatch, queued_response
):
    import os
    import signal

    from redis.connection import Connection
    from rq.worker import WorkerStatus

    queue = get_queue("ingestion")
    worker = consumer([queue])
    worker.register_birth()
    worker._state = WorkerStatus.IDLE
    waiting = queue.enqueue("builtins.str", "must remain queued")
    original = Connection.read_response
    responses = []

    def interrupted(connection, *args, **kwargs):
        value = original(connection, *args, **kwargs)
        if value == b"QUEUED":
            responses.append(value)
            if len(responses) == queued_response:
                os.kill(os.getpid(), signal.SIGTERM)
        return value

    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, worker.request_stop)
    try:
        monkeypatch.setattr(Connection, "read_response", interrupted)
        with queue.connection.pipeline(transaction=True) as pipe:
            pipe.set("devfeed:shutdown-proof", "protocol-clean")
            pipe.get("devfeed:shutdown-proof")
            assert pipe.execute() == [True, b"protocol-clean"]
        assert worker._stop_requested
        worker.teardown()
        assert queue.connection.ping()
        assert queue.job_ids == [waiting.id]
        assert queue.connection.hexists(worker.key, "death")
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        queue.connection.close()


@pytest.mark.failure_recovery
def test_warm_shutdown_returns_from_idle_blocking_redis_without_waiting_for_work(
    database, monkeypatch
):
    import os
    import signal
    import threading
    import time

    queue = get_queue("ingestion")
    worker = consumer([queue])
    worker.register_birth()
    monkeypatch.setattr(analysis_worker, "SHUTDOWN_CHECK_INTERVAL", 1)
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, worker.request_stop)
    timer = threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.daemon = True
    started = time.monotonic()
    try:
        timer.start()
        assert worker.dequeue_job_and_maintain_ttl(75) is None
        assert worker._stop_requested and time.monotonic() - started < 2
        worker.teardown()
        assert queue.connection.ping()
    finally:
        timer.cancel()
        timer.join()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        queue.connection.close()


@pytest.mark.failure_recovery
def test_warm_shutdown_leaves_rq_connection_retry_without_requiring_redis_recovery(
    database, monkeypatch
):
    import os
    import signal
    import socket
    import threading
    import time

    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    queue = get_queue("ingestion")
    waiting = queue.enqueue("builtins.str", "preserved through the Redis outage")
    worker = consumer([queue])
    worker.register_birth()
    connected = worker.connection
    dequeue = worker.queue_class.dequeue_any
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, worker.request_stop)
    request = threading.Timer(0.01, lambda: os.kill(os.getpid(), signal.SIGTERM))
    request.daemon = True
    unavailable = socket.socket()
    unavailable.bind(("127.0.0.1", 0))  # Reserve a real port that refuses connections.
    disconnected = Redis(
        host="127.0.0.1",
        port=unavailable.getsockname()[1],
        db=15,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
        retry=Retry(NoBackoff(), 0),
    )
    failures = []

    def lose_connection(queues, timeout, **kwargs):
        # Start the outage inside the real RQ retry loop, after state registration
        # and its successful heartbeat, using an actual unreachable TCP endpoint.
        worker.connection = disconnected
        failures.append(True)
        request.start()
        kwargs["connection"] = disconnected
        return dequeue(queues, timeout, **kwargs)

    monkeypatch.setattr(worker.queue_class, "dequeue_any", staticmethod(lose_connection))
    started = time.monotonic()
    try:
        assert worker.dequeue_job_and_maintain_ttl(75) is None
        assert worker._stop_requested and failures == [True]
        assert time.monotonic() - started < 3
        assert queue.job_ids == [waiting.id]
        worker.connection = connected
        worker.teardown()
        assert queue.connection.ping()
    finally:
        request.cancel()
        request.join()
        worker.connection = connected
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        disconnected.close()
        unavailable.close()
        queue.connection.close()


@pytest.mark.failure_recovery
def test_warm_shutdown_after_dequeue_drains_the_assigned_job_before_teardown(database, monkeypatch):
    import os
    import signal

    from rq.job import JobStatus

    queue = get_queue("ingestion")
    assigned = queue.enqueue("builtins.str", "finish the assigned job", result_ttl=60)
    following = queue.enqueue("builtins.str", "leave the next job queued", result_ttl=60)
    worker = consumer([queue])
    worker.register_birth()
    dequeue = worker.queue_class.dequeue_any
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, worker.request_stop)

    def request_after_assignment(queues, timeout, **kwargs):
        result = dequeue(queues, timeout, **kwargs)
        assert result is not None
        os.kill(os.getpid(), signal.SIGTERM)
        return result

    monkeypatch.setattr(worker.queue_class, "dequeue_any", staticmethod(request_after_assignment))
    try:
        job, selected = worker.dequeue_job_and_maintain_ttl(75)
        assert worker._stop_requested and job.id == assigned.id
        worker.execute_job(job, selected)
        assert assigned.get_status() == JobStatus.FINISHED
        assert assigned.return_value() == "finish the assigned job"
        assert queue.job_ids == [following.id]
        worker.teardown()
        assert queue.connection.ping()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        queue.connection.close()


@pytest.mark.failure_recovery
def test_dequeue_outage_and_shutdown_preserve_intermediate_delivery_for_republication(
    database, monkeypatch
):
    import os
    import signal
    import socket
    from datetime import timedelta

    from devfeed_core.models import utcnow
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from rq.job import JobStatus

    queue = get_queue("ingestion")
    assigned = queue.enqueue(
        "builtins.str",
        "retained delivery",
        job_id="shutdown-intermediate-" + uuid.uuid4().hex,
        unique=True,
        result_ttl=60,
    )
    following = queue.enqueue("builtins.str", "next delivery", result_ttl=60)
    worker = consumer([queue])
    worker.register_birth()
    worker.get_redis_server_version()
    connected = worker.connection
    dequeue = worker.queue_class.dequeue_any
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, worker.request_stop)
    unavailable = socket.socket()
    unavailable.bind(("127.0.0.1", 0))
    disconnected = Redis(
        host="127.0.0.1",
        port=unavailable.getsockname()[1],
        db=15,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
        retry=Retry(NoBackoff(), 0),
    )

    def disconnect_after_assignment(queues, timeout, **kwargs):
        result = dequeue(queues, timeout, **kwargs)
        assert result is not None
        worker.connection = disconnected
        os.kill(os.getpid(), signal.SIGTERM)
        return result

    monkeypatch.setattr(
        worker.queue_class, "dequeue_any", staticmethod(disconnect_after_assignment)
    )
    try:
        assert worker.dequeue_job_and_maintain_ttl(75) is None
        assert worker._stop_requested
        assert queue.job_ids == [following.id]
        assert queue.intermediate_queue.get_job_ids() == [assigned.id]
        assert assigned.get_status() == JobStatus.QUEUED
        worker.connection = connected
        worker.teardown()
        # The ordinary RQ stale-intermediate cleanup and durable republisher
        # recover this pre-execution transport failure after their normal delay.
        queue.connection.set(
            queue.intermediate_queue.get_first_seen_key(assigned.id),
            (utcnow() - timedelta(minutes=2)).timestamp(),
        )
        queue.intermediate_queue.cleanup(worker, queue)
        assert assigned.get_status() == JobStatus.FAILED
        assert queue.intermediate_queue.get_job_ids() == []
        restored = queue.enqueue(
            "builtins.str", "retained delivery", job_id=assigned.id, unique=True, result_ttl=60
        )
        assert restored.id == assigned.id
        assert queue.job_ids == [following.id, assigned.id]
    finally:
        worker.connection = connected
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        disconnected.close()
        unavailable.close()
        queue.connection.close()
