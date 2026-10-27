"""Worker policy without Redis delivery or database transaction simulations."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from devfeed_aggregator import analysis_worker, codex_client
from opentelemetry.trace import StatusCode
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.worker import WorkerStatus


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        value.sleeps.append(seconds)
        value.now += seconds

    monkeypatch.setattr(
        analysis_worker, "time", SimpleNamespace(monotonic=lambda: value.now, sleep=sleep)
    )
    return value


@pytest.fixture
def worker_factory(monkeypatch):
    """Real RQ objects; only Redis reads/writes and the Codex RPC are boundaries."""
    connection = Redis(host="redis.invalid", db=15)
    depths = {}
    monkeypatch.setattr(Queue, "count", property(lambda queue: depths.get(queue.name, 0)))
    monkeypatch.setattr(
        codex_client, "CodexClient", Mock(return_value=Mock(readiness=lambda: None))
    )
    monkeypatch.setattr(analysis_worker, "pause_reason", lambda _: None)
    monkeypatch.setattr(analysis_worker, "cooldown_remaining", lambda _: 0)

    def build(*names):
        queues = [Queue(name, connection=connection) for name in names]
        worker = analysis_worker.AnalysisAwareWorker(
            queues=queues, connection=connection, prepare_for_work=False, worker_ttl=120
        )
        worker.check_for_suspension = Mock()
        worker.set_state = Mock(side_effect=lambda state: setattr(worker, "_state", state))
        worker.procline = Mock()
        worker.heartbeat = Mock()
        worker.run_maintenance_tasks = Mock()
        return worker

    yield build, depths
    connection.close()


def test_readiness_backs_off_outages_but_rechecks_each_pending_delivery(clock, monkeypatch):
    rpc = Mock(side_effect=["codex_unavailable", None, None, "codex_unavailable"])
    monkeypatch.setattr(codex_client, "CodexClient", Mock(return_value=Mock(readiness=rpc)))
    readiness = analysis_worker.CodexReadiness()

    assert not readiness.ready(pending=True)
    clock.now = 9
    assert not readiness.ready(pending=True)
    assert rpc.call_count == 1
    clock.now = 10
    assert readiness.ready(pending=False)
    clock.now = 11
    assert readiness.ready(pending=False)
    assert rpc.call_count == 2
    assert readiness.ready(pending=True)
    assert not readiness.ready(pending=True)
    assert readiness.reason == "codex_unavailable" and rpc.call_count == 4


@pytest.mark.parametrize("barrier", ["quota", "capacity", "unavailable"])
def test_shared_ai_barriers_leave_background_queues_eligible(
    worker_factory, monkeypatch, clock, barrier
):
    build, depths = worker_factory
    worker = build("article-analysis", "ingestion", "notifications")
    depths["article-analysis"] = 2
    probe = Mock(side_effect=AssertionError("Codex must not be probed during a shared pause"))
    worker.codex_readiness.ready = probe
    if barrier == "quota":
        monkeypatch.setattr(analysis_worker, "pause_reason", lambda _: "quota_budget_exhausted")
        reason = "quota_budget_exhausted"
    elif barrier == "capacity":
        monkeypatch.setattr(analysis_worker, "cooldown_remaining", lambda _: 30)
        reason = "provider_capacity_cooldown"
    else:
        monkeypatch.setattr(
            analysis_worker, "pause_reason", Mock(side_effect=RedisError("offline"))
        )
        reason = "capacity_check_unavailable"
    selected = []

    def consume(instance, timeout, max_idle_time=None):
        selected.extend(queue.name for queue in instance._ordered_queues)
        assert timeout is None
        return "background-job", instance._ordered_queues[0]

    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    original = worker._ordered_queues
    result = worker.dequeue_job_and_maintain_ttl(timeout=10)

    assert selected == ["ingestion", "notifications"]
    assert result[0] == "background-job"
    assert worker._ordered_queues is original
    assert worker.analysis_paused and worker.codex_readiness.reason == reason
    probe.assert_not_called()


def test_only_nonempty_ready_ai_lanes_are_exposed_to_nonblocking_consumer(
    worker_factory, monkeypatch, clock, caplog
):
    build, depths = worker_factory
    worker = build("topic-analysis", "article-analysis", "images")
    depths.update({"article-analysis": 1, "topic-analysis": 0})
    worker.analysis_paused = True
    worker.codex_readiness.ready = Mock(return_value=True)
    selected = []

    def consume(instance, timeout, max_idle_time=None):
        selected.extend(queue.name for queue in instance._ordered_queues)
        assert timeout is None
        return "ready-job", instance._ordered_queues[0]

    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    with caplog.at_level("INFO", logger=analysis_worker.__name__):
        result = worker.dequeue_job_and_maintain_ttl(timeout=30)

    assert result[1].name == "article-analysis"
    assert selected == ["article-analysis", "images"]
    worker.codex_readiness.ready.assert_called_once_with(pending=True)
    assert not worker.analysis_paused
    assert "analysis_queue_resumed" in caplog.messages


@pytest.mark.parametrize(
    "ready,state", [(True, WorkerStatus.IDLE), (False, WorkerStatus.SUSPENDED)]
)
def test_empty_dedicated_worker_heartbeats_and_exits_burst_without_dequeue(
    worker_factory, monkeypatch, clock, ready, state
):
    build, _ = worker_factory
    worker = build("source-analysis")
    worker.codex_readiness.ready = Mock(return_value=ready)
    consume = Mock(side_effect=AssertionError("No eligible queue to consume"))
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)

    assert worker.dequeue_job_and_maintain_ttl(timeout=None) is None
    assert worker.get_state() == state
    worker.codex_readiness.ready.assert_called_once_with(pending=False)
    worker.heartbeat.assert_called_once_with()
    worker.run_maintenance_tasks.assert_called_once_with()
    assert clock.sleeps == []


def test_paused_polling_honors_fractional_idle_budget(worker_factory, clock):
    build, _ = worker_factory
    worker = build("research-verification")
    worker.codex_readiness.ready = Mock(return_value=False)
    assert worker.dequeue_job_and_maintain_ttl(timeout=30, max_idle_time=2.25) is None
    assert clock.sleeps == [1, 1, 0.25]
    assert worker.heartbeat.call_count == 4


def test_shutdown_during_readiness_cannot_start_a_job(worker_factory, monkeypatch, clock):
    build, depths = worker_factory
    worker = build("article-analysis")
    depths["article-analysis"] = 1

    def stop(*, pending):
        assert pending
        worker._stop_requested = True
        return True

    worker.codex_readiness.ready = stop
    consume = Mock()
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    assert worker.dequeue_job_and_maintain_ttl(timeout=30) is None
    consume.assert_not_called()


def test_consumer_failure_restores_full_queue_order(worker_factory, monkeypatch, clock):
    build, depths = worker_factory
    worker = build("article-analysis", "images")
    depths["article-analysis"] = 1
    original = worker._ordered_queues
    monkeypatch.setattr(
        Worker, "dequeue_job_and_maintain_ttl", Mock(side_effect=RedisError("disconnected"))
    )
    with pytest.raises(RedisError, match="disconnected"):
        worker.dequeue_job_and_maintain_ttl(timeout=None)
    assert worker._ordered_queues is original


def test_background_worker_preserves_rq_blocking_parameters(worker_factory, monkeypatch):
    build, _ = worker_factory
    worker = build("images")
    assert worker.codex_readiness is None
    consume = Mock(return_value=("image-job", worker.queues[0]))
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    assert worker.dequeue_job_and_maintain_ttl(15, max_idle_time=90)[0] == "image-job"
    consume.assert_called_once_with(15, 90)


@pytest.mark.parametrize("state,ttl", [(WorkerStatus.IDLE, 165), (WorkerStatus.BUSY, 90)])
def test_health_file_is_atomic_and_accounts_for_blocking_dequeue(
    worker_factory, monkeypatch, clock, tmp_path, state, ttl
):
    build, _ = worker_factory
    worker = build("images")
    worker._state = state
    target = tmp_path / "worker.json"
    target.write_text("old report")
    monkeypatch.setenv("DEVFEED_WORKER_HEALTH_PATH", str(target))
    clock.now = 123.5
    worker.write_health()
    assert json.loads(target.read_text()) == {"pid": worker.health_pid, "at": 123.5, "ttl": ttl}
    assert not target.with_suffix(".tmp").exists()


def test_workhorse_cannot_refresh_parent_health_file(worker_factory, monkeypatch, tmp_path):
    build, _ = worker_factory
    worker = build("images")
    target = tmp_path / "worker.json"
    target.write_text("parent report")
    monkeypatch.setenv("DEVFEED_WORKER_HEALTH_PATH", str(target))
    monkeypatch.setattr(analysis_worker.os, "getpid", lambda: worker.health_pid + 1)
    worker.write_health()
    assert target.read_text() == "parent report"


def test_health_updates_follow_confirmed_heartbeats(worker_factory, monkeypatch):
    build, _ = worker_factory
    worker = build("images")
    worker.write_health = Mock()
    heartbeat = Mock(return_value="confirmed")
    monkeypatch.setattr(Worker, "heartbeat", heartbeat)
    monkeypatch.setattr(Worker, "maintain_heartbeats", Mock())
    # The fixture replaces effects on the instance; invoke the real orchestration method.
    pipeline = object()
    assert analysis_worker.AnalysisAwareWorker.heartbeat(worker, 45, pipeline) == "confirmed"
    worker.write_health.assert_not_called()
    analysis_worker.AnalysisAwareWorker.maintain_heartbeats(worker, object())
    worker.write_health.assert_called_once_with()
    analysis_worker.AnalysisAwareWorker.heartbeat(worker, 60)
    assert worker.write_health.call_count == 2
    assert heartbeat.call_args_list == [
        call(timeout=45, pipeline=pipeline),
        call(timeout=60, pipeline=None),
    ]


@pytest.mark.parametrize("name", ["images", "custom-private-queue"])
@pytest.mark.parametrize("failure", [False, True])
def test_job_metrics_reset_and_count_failures_without_unbounded_queue_labels(
    worker_factory, monkeypatch, clock, name, failure
):
    build, _ = worker_factory
    worker = build(name)
    instruments = {key: Mock() for key in ["worker_active", "executions", "execution_duration"]}
    monkeypatch.setattr(
        analysis_worker, "current", lambda: SimpleNamespace(instruments=instruments)
    )

    def execute(*args):
        clock.now += 3.5
        if failure:
            raise RuntimeError("workhorse failure")
        return "completed"

    monkeypatch.setattr(Worker, "execute_job", execute)
    if failure:
        with pytest.raises(RuntimeError, match="workhorse failure"):
            worker.execute_job(object(), worker.queues[0])
    else:
        assert worker.execute_job(object(), worker.queues[0]) == "completed"
    labels = {"messaging.destination.name": name if name == "images" else "other"}
    assert instruments["worker_active"].set.call_args_list == [call(1), call(0)]
    instruments["executions"].add.assert_called_once_with(1, labels)
    instruments["execution_duration"].record.assert_called_once_with(3.5, labels)


@pytest.mark.parametrize("result", [True, False, "raises"])
def test_workhorse_owns_telemetry_lifetime_and_reports_failed_execution(
    worker_factory, monkeypatch, result
):
    build, _ = worker_factory
    worker = build("images")
    runtime, execution_span = object(), Mock()
    start = Mock(return_value=runtime)
    stop = Mock()
    observed = {}

    @contextmanager
    def process_span(name, **kwargs):
        observed.update(name=name, **kwargs)
        yield execution_span

    monkeypatch.setattr(analysis_worker, "start_runtime", start)
    monkeypatch.setattr(analysis_worker, "stop_runtime", stop)
    drained = Mock()
    monkeypatch.setattr(analysis_worker, "drain_analytics", drained)
    monkeypatch.setattr(analysis_worker, "span", process_span)
    monkeypatch.setattr(analysis_worker, "extract_context", lambda carrier: ("context", carrier))
    monkeypatch.setattr(
        Worker,
        "perform_job",
        Mock(side_effect=RuntimeError("job failed"))
        if result == "raises"
        else Mock(return_value=result),
    )
    job = SimpleNamespace(meta={"devfeed_trace": {"traceparent": "parent"}})
    if result == "raises":
        with pytest.raises(RuntimeError, match="job failed"):
            worker.perform_job(job, worker.queues[0])
    else:
        assert worker.perform_job(job, worker.queues[0]) is result
    start.assert_called_once_with("worker-images", serve_metrics=False)
    drained.assert_called_once_with()
    stop.assert_called_once_with(runtime)
    assert observed["name"] == "process images"
    assert observed["context"] == ("context", {"traceparent": "parent"})
    assert observed["attributes"]["messaging.operation.name"] == "process"
    if result is False:
        execution_span.set_status.assert_called_once_with(StatusCode.ERROR)
    else:
        execution_span.set_status.assert_not_called()
