"""Worker policy without Redis delivery or database transaction simulations."""

from contextlib import contextmanager
from pathlib import Path
from stat import S_IMODE
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
        analysis_worker,
        "time",
        SimpleNamespace(
            monotonic=lambda: value.now,
            sleep=sleep,
            clock_gettime=lambda clock: value.now,
            CLOCK_BOOTTIME=7,
        ),
    )
    return value


@pytest.fixture
def worker_factory(monkeypatch):
    """Real RQ objects; only Redis reads/writes and the Codex RPC are boundaries."""
    connection = Redis(host="redis.invalid", db=15)
    depths = {}
    monkeypatch.setattr(
        codex_client, "CodexClient", Mock(return_value=Mock(readiness=lambda: None))
    )

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
        worker.admission_snapshot = lambda: analysis_worker.AdmissionSnapshot(
            {name: bool(depths.get(name)) for name in names}
        )
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


@pytest.mark.parametrize("barrier", ["quota", "capacity"])
def test_shared_ai_barriers_leave_background_queues_eligible(
    worker_factory, monkeypatch, clock, barrier
):
    build, depths = worker_factory
    worker = build("article-analysis", "ingestion", "notifications")
    depths["article-analysis"] = 2
    depths.update(ingestion=1, notifications=1)
    probe = Mock(side_effect=AssertionError("Codex must not be probed during a shared pause"))
    worker.codex_readiness.ready = probe
    reason = "quota_budget_exhausted" if barrier == "quota" else "provider_capacity_cooldown"
    worker.admission_snapshot = lambda: analysis_worker.AdmissionSnapshot(depths, reason)
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
    depths.update({"article-analysis": 1, "topic-analysis": 0, "images": 1})
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


@pytest.mark.parametrize("previously_paused", [False, True])
def test_empty_dedicated_worker_heartbeats_and_exits_burst_without_dequeue(
    worker_factory, monkeypatch, clock, previously_paused
):
    build, _ = worker_factory
    worker = build("source-analysis")
    worker.analysis_paused = previously_paused
    worker.codex_readiness.ready = Mock(side_effect=AssertionError("No AI work needs admission"))
    consume = Mock(side_effect=AssertionError("No eligible queue to consume"))
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)

    assert worker.dequeue_job_and_maintain_ttl(timeout=None) is None
    assert worker.get_state() == WorkerStatus.IDLE
    worker.codex_readiness.ready.assert_not_called()
    worker.run_maintenance_tasks.assert_called_once_with()
    assert clock.sleeps == []


def test_paused_polling_honors_fractional_idle_budget(worker_factory, clock):
    build, depths = worker_factory
    worker = build("research-verification")
    depths["research-verification"] = 1
    worker.codex_readiness.ready = Mock(return_value=False)
    assert worker.dequeue_job_and_maintain_ttl(timeout=30, max_idle_time=2.25) is None
    assert clock.sleeps == [1, 1, 0.25]
    assert worker.set_state.call_count == 1


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
    consume.assert_called_once_with(10, 10)


def test_background_dequeue_rechecks_stop_between_bounded_blocking_calls(
    worker_factory, monkeypatch, clock
):
    build, _ = worker_factory
    worker = build("ingestion")
    calls = []

    def consume(instance, timeout, max_idle_time):
        calls.append((timeout, max_idle_time))
        clock.now += max_idle_time
        return None

    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    assert worker.dequeue_job_and_maintain_ttl(75, max_idle_time=22.5) is None
    assert calls == [(10, 10), (10, 10), (2.5, 2.5)]
    worker._shutdown()
    assert worker.dequeue_job_and_maintain_ttl(75) is None
    assert len(calls) == 3


def test_burst_background_dequeue_retains_nonblocking_rq_contract(worker_factory, monkeypatch):
    build, _ = worker_factory
    worker = build("ingestion")
    consume = Mock(return_value=None)
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    assert worker.dequeue_job_and_maintain_ttl(None, max_idle_time=20) is None
    consume.assert_called_once_with(None, 20)


def test_idle_ai_workers_skip_stock_dequeue_and_provider_connections(worker_factory, clock):
    build, _ = worker_factory
    worker = build("article-analysis", "relationships")
    worker.codex_readiness.ready = Mock(side_effect=AssertionError("No pending inference"))
    assert worker.dequeue_job_and_maintain_ttl(30, max_idle_time=3.25) is None
    worker.codex_readiness.ready.assert_not_called()
    assert clock.sleeps == [1, 1, 1, 0.25]
    worker.set_state.assert_called_once_with(WorkerStatus.IDLE)


def test_redis_inspection_failure_never_consumes_or_refreshes_health(
    worker_factory, monkeypatch, clock
):
    build, depths = worker_factory
    worker = build("article-analysis", "ingestion")
    depths.update({"article-analysis": 1, "ingestion": 1})
    worker.admission_snapshot = Mock(side_effect=RedisError("offline"))
    consume = Mock(side_effect=AssertionError("No confirmed admission"))
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    worker.write_health = Mock()
    assert worker.dequeue_job_and_maintain_ttl(30, max_idle_time=2) is None
    consume.assert_not_called()
    worker.write_health.assert_not_called()
    worker.set_state.assert_not_called()
    worker.codex_readiness.ready = Mock()
    worker._stop_requested = True
    assert worker.dequeue_job_and_maintain_ttl(30) is None
    worker.codex_readiness.ready.assert_not_called()


@pytest.mark.parametrize("burst", [False, True])
def test_rq_suspension_blocks_every_queue_without_provider_admission(
    worker_factory, monkeypatch, clock, burst
):
    from rq.exceptions import StopRequested

    build, _ = worker_factory
    worker = build("article-analysis", "ingestion")
    worker.admission_snapshot = lambda: analysis_worker.AdmissionSnapshot(
        {"article-analysis": True, "ingestion": True}, suspended=True
    )
    worker.codex_readiness.ready = Mock(side_effect=AssertionError("RQ suspended"))
    consume = Mock()
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", consume)
    if burst:
        with pytest.raises(StopRequested):
            worker.dequeue_job_and_maintain_ttl(None)
    else:
        assert worker.dequeue_job_and_maintain_ttl(10, max_idle_time=1) is None
        worker.set_state.assert_called_once_with(WorkerStatus.SUSPENDED)
    consume.assert_not_called()


def test_global_rq_suspension_stops_after_warm_shutdown(worker_factory, monkeypatch, clock):
    build, _ = worker_factory
    worker = build("article-analysis", "ingestion")
    worker.admission_snapshot = lambda: analysis_worker.AdmissionSnapshot(
        {"article-analysis": True, "ingestion": True}, suspended=True
    )
    worker.codex_readiness.ready = Mock(side_effect=AssertionError("RQ suspended"))
    monkeypatch.setattr(analysis_worker.time, "sleep", lambda _: worker._shutdown())
    assert worker.dequeue_job_and_maintain_ttl(75) is None
    assert worker._stop_requested
    worker.set_state.assert_called_once_with(WorkerStatus.SUSPENDED)
    worker.codex_readiness.ready.assert_not_called()


def test_admission_batches_queue_barriers_and_confirmed_heartbeats(
    worker_factory, clock, monkeypatch
):
    build, _ = worker_factory
    worker = build("article-analysis", "ingestion")
    pipe = Mock()
    worker.connection = Mock()
    worker.connection.pipeline.return_value.__enter__ = Mock(return_value=pipe)
    worker.connection.pipeline.return_value.__exit__ = Mock(return_value=False)
    worker.write_health = Mock()
    monkeypatch.setattr(analysis_worker, "snapshot_pause_reason", lambda *_args, **_kwargs: None)
    pipe.execute.return_value = [1, True, 2, 0, None, 300, 0]
    inspect = analysis_worker.AnalysisAwareWorker.admission_snapshot
    first = inspect(worker)
    assert first.pending == {"article-analysis": True, "ingestion": False}
    assert first.reason == "provider_capacity_cooldown" and not first.suspended
    worker.connection.pipeline.assert_called_once_with(transaction=False)
    assert pipe.llen.call_args_list == [call(queue.key) for queue in worker.queues]
    worker.heartbeat.assert_called_once_with(pipeline=pipe)
    worker.write_health.assert_called_once_with()
    clock.now = 29
    pipe.execute.return_value = [0, 1, None, -2, 1]
    second = inspect(worker)
    assert second.pending == {"article-analysis": False, "ingestion": True}
    assert second.reason is None and second.suspended
    assert worker.heartbeat.call_count == worker.write_health.call_count == 1
    clock.now = 30
    pipe.execute.side_effect = RedisError("connection lost")
    with pytest.raises(RedisError):
        inspect(worker)
    assert worker.write_health.call_count == 1
    assert worker.next_admission_heartbeat == 30


@pytest.mark.parametrize("state,ttl", [(WorkerStatus.IDLE, 165), (WorkerStatus.BUSY, 90)])
def test_health_file_is_atomic_and_accounts_for_blocking_dequeue(
    worker_factory, monkeypatch, clock, tmp_path, state, ttl
):
    build, _ = worker_factory
    worker = build("images")
    worker._state = state
    target = tmp_path / "worker-health"
    target.write_text("old report")
    monkeypatch.setenv("DEVFEED_WORKER_HEALTH_PATH", str(target))
    clock.now = 123.5
    boot_clock = Mock(return_value=123.5)
    monkeypatch.setattr(analysis_worker.time, "clock_gettime", boot_clock)
    replace = Path.replace
    replacements = []

    def inspect_then_replace(staged, destination):
        assert target.read_text() == "old report"
        assert staged.read_text() == f"{worker.health_pid} 123 {ttl}\n"
        assert staged.parent == target.parent
        assert S_IMODE(staged.stat().st_mode) == 0o600
        replacements.append(staged)
        return replace(staged, destination)

    monkeypatch.setattr(Path, "replace", inspect_then_replace)
    worker.write_health()
    assert target.read_text() == f"{worker.health_pid} 123 {ttl}\n"
    assert S_IMODE(target.stat().st_mode) == 0o600
    boot_clock.assert_called_once_with(analysis_worker.time.CLOCK_BOOTTIME)
    assert len(replacements) == 1 and not replacements[0].exists()
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("link_location", ["temporary", "target"])
def test_health_publication_does_not_follow_preexisting_symlinks(
    worker_factory, monkeypatch, clock, tmp_path, link_location
):
    build, _ = worker_factory
    worker = build("images")
    worker._state = WorkerStatus.BUSY
    target = tmp_path / "worker-health"
    victim = tmp_path / "unrelated-file"
    victim.write_text("keep unrelated data")
    link = target.with_suffix(".tmp") if link_location == "temporary" else target
    link.symlink_to(victim)
    monkeypatch.setenv("DEVFEED_WORKER_HEALTH_PATH", str(target))
    clock.now = 123.5

    worker.write_health()

    assert victim.read_text() == "keep unrelated data"
    assert not target.is_symlink()
    assert target.read_text() == f"{worker.health_pid} 123 90\n"
    assert S_IMODE(target.stat().st_mode) == 0o600
    if link_location == "temporary":
        assert link.is_symlink() and link.resolve() == victim


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
@pytest.mark.parametrize("drain_seconds", [17, 45])
def test_workhorse_owns_telemetry_lifetime_and_reports_failed_execution(
    worker_factory, monkeypatch, result, drain_seconds
):
    build, _ = worker_factory
    worker = build("images")
    runtime, execution_span = object(), Mock()
    monkeypatch.setattr(
        analysis_worker.get_settings(), "analytics_worker_drain_seconds", drain_seconds
    )
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
    drained.assert_called_once_with(timeout=drain_seconds)
    stop.assert_called_once_with(runtime)
    assert observed["name"] == "process images"
    assert observed["context"] == ("context", {"traceparent": "parent"})
    assert observed["attributes"]["messaging.operation.name"] == "process"
    if result is False:
        execution_span.set_status.assert_called_once_with(StatusCode.ERROR)
    else:
        execution_span.set_status.assert_not_called()


@pytest.mark.parametrize("state", [WorkerStatus.IDLE, WorkerStatus.SUSPENDED, WorkerStatus.BUSY])
@pytest.mark.parametrize("pipelined", [False, True])
@pytest.mark.parametrize("unavailable", [False, True])
def test_warm_shutdown_cancels_only_failed_nonbusy_heartbeat_exchanges(
    worker_factory, monkeypatch, state, pipelined, unavailable
):
    from redis.exceptions import ConnectionError as RedisConnectionError
    from rq.exceptions import StopRequested

    build, _ = worker_factory
    worker = build("ingestion")
    worker._state = state
    worker._shutdown()
    heartbeat = Mock(side_effect=RedisConnectionError("unreachable") if unavailable else None)
    monkeypatch.setattr(Worker, "heartbeat", heartbeat)
    worker.write_health = Mock()
    pipeline = object() if pipelined else None
    failure = (
        StopRequested
        if unavailable and state != WorkerStatus.BUSY and not pipelined
        else RedisConnectionError
        if unavailable
        else None
    )
    if failure:
        with pytest.raises(failure):
            analysis_worker.AnalysisAwareWorker.heartbeat(worker, pipeline=pipeline)
        worker.write_health.assert_not_called()
    else:
        analysis_worker.AnalysisAwareWorker.heartbeat(worker, pipeline=pipeline)
        assert worker.write_health.call_count == int(not pipelined)
    heartbeat.assert_called_once_with(timeout=None, pipeline=pipeline)


@pytest.mark.parametrize("stopping", [False, True])
def test_heartbeat_never_masks_protocol_errors_or_unrequested_connection_failures(
    worker_factory, monkeypatch, stopping
):
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import ResponseError

    build, _ = worker_factory
    worker = build("ingestion")
    worker._state = WorkerStatus.IDLE
    worker._stop_requested = stopping
    failures = [ResponseError("protocol failure")]
    if not stopping:
        failures.append(RedisConnectionError("network failure"))
    for error in failures:
        monkeypatch.setattr(Worker, "heartbeat", Mock(side_effect=error))
        with pytest.raises(type(error), match=str(error)):
            analysis_worker.AnalysisAwareWorker.heartbeat(worker)


@pytest.mark.parametrize("requested", [False, True])
def test_background_dequeue_translates_only_its_own_safe_shutdown(
    worker_factory, monkeypatch, requested
):
    from rq.exceptions import StopRequested

    build, _ = worker_factory
    worker = build("ingestion")

    def interrupted(*args):
        if requested:
            worker._shutdown()
        raise StopRequested()

    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", interrupted)
    if requested:
        assert worker.dequeue_job_and_maintain_ttl(75) is None
    else:
        with pytest.raises(StopRequested):
            worker.dequeue_job_and_maintain_ttl(75)


@pytest.mark.parametrize(
    "metadata,origin,handler,expected",
    [
        ({"devfeed_queue": "article-analysis-fresh"}, "high", None, "article-analysis-fresh"),
        ({"devfeed_queue": "partner-evaluation"}, "default", None, "partner-evaluation"),
        ({}, "relationships", None, "relationships"),
        (
            {},
            "default",
            "devfeed_aggregator.partner_tasks.process_evaluation",
            "partner-evaluation",
        ),
        ({}, "default", "unknown.private.handler", "other"),
    ],
)
def test_execution_policy_uses_logical_metadata_before_physical_priority(
    metadata, origin, handler, expected
):
    job = SimpleNamespace(meta=metadata, origin=origin, func_name=handler)
    assert analysis_worker.logical_job_queue(job) == expected


def priority_worker(monkeypatch, worker_factory):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "job_queue_overrides", {})
    build, depths = worker_factory
    depths["default"] = 1
    worker = build("default")
    monkeypatch.setattr(worker, "get_redis_server_version", lambda: (8, 0, 0))
    return worker


def queued_delivery(identifier, logical):
    return SimpleNamespace(
        id=identifier,
        key=f"rq:job:{identifier}",
        meta={"devfeed_queue": logical},
        origin="default",
        func_name="builtins.str",
    )


def test_priority_scan_reaches_io_behind_more_than_one_page_of_paused_ai(
    monkeypatch, worker_factory
):
    worker = priority_worker(monkeypatch, worker_factory)
    worker.codex_readiness.ready = Mock(return_value=False)
    worker.codex_readiness.reason = "codex_unavailable"
    jobs = {
        f"ai-{index}": queued_delivery(f"ai-{index}", "article-analysis") for index in range(150)
    }
    jobs["io"] = queued_delivery("io", "ingestion")
    identifiers = list(jobs)
    scans = []

    def read(key, start, end):
        scans.append((start, end))
        return identifiers[start : end + 1]

    monkeypatch.setattr(worker.connection, "lrange", read)
    claim = Mock(return_value=1)
    monkeypatch.setattr(worker.connection, "eval", claim)
    monkeypatch.setattr(
        worker.job_class,
        "fetch_many",
        lambda identifiers, **kwargs: [jobs[key] for key in identifiers],
    )
    snapshot = analysis_worker.AdmissionSnapshot({"default": True})
    assert worker.dequeue_priority(snapshot) is None
    result = worker.dequeue_priority(snapshot)
    assert result[0].id == "io"
    assert scans == [(0, 99), (100, 199)]
    assert worker.codex_readiness.ready.call_count == 2
    # No paused delivery was removed or requeued; only the admitted IO job moves.
    assert claim.call_count == 1
    assert claim.call_args.args[-2:] == ("io", "default")


def test_priority_quota_gate_skips_ai_but_executes_io_without_clearing_ai_work(
    monkeypatch, worker_factory
):
    worker = priority_worker(monkeypatch, worker_factory)
    snapshot = analysis_worker.AdmissionSnapshot({"default": True}, "quota_budget_exhausted")
    worker.admission_snapshot = lambda: snapshot
    worker.codex_readiness.ready = Mock(side_effect=AssertionError("quota must gate the RPC"))
    jobs = {"ai": queued_delivery("ai", "topic-analysis"), "io": queued_delivery("io", "ingestion")}
    monkeypatch.setattr(worker.connection, "lrange", lambda *args: list(jobs))
    claim = Mock(return_value=1)
    monkeypatch.setattr(worker.connection, "eval", claim)
    monkeypatch.setattr(
        worker.job_class,
        "fetch_many",
        lambda identifiers, **kwargs: [jobs[key] for key in identifiers],
    )
    assert worker.dequeue_priority(snapshot)[0].id == "io"
    worker.codex_readiness.ready.assert_not_called()
    assert claim.call_args.args[-2:] == ("io", "default")


def test_priority_claim_race_does_not_execute_a_delivery_claimed_by_another_worker(
    monkeypatch, worker_factory
):
    worker = priority_worker(monkeypatch, worker_factory)
    jobs = {
        "first": queued_delivery("first", "ingestion"),
        "second": queued_delivery("second", "ingestion"),
    }
    monkeypatch.setattr(worker.connection, "lrange", lambda *args: list(jobs))
    claim = Mock(side_effect=[0, 1])
    monkeypatch.setattr(worker.connection, "eval", claim)
    monkeypatch.setattr(
        worker.job_class,
        "fetch_many",
        lambda identifiers, **kwargs: [jobs[key] for key in identifiers],
    )
    assert (
        worker.dequeue_priority(analysis_worker.AdmissionSnapshot({"default": True}))[0].id
        == "second"
    )
    assert claim.call_count == 2


def test_priority_ai_claim_requires_a_fresh_readiness_check_for_each_delivery(
    monkeypatch, worker_factory
):
    worker = priority_worker(monkeypatch, worker_factory)
    job = queued_delivery("ai", "partner-evaluation")
    monkeypatch.setattr(worker.connection, "lrange", lambda *args: ["ai"])
    monkeypatch.setattr(worker.connection, "eval", lambda *args: 1)
    monkeypatch.setattr(worker.job_class, "fetch_many", lambda *args, **kwargs: [job])
    worker.codex_readiness.ready = Mock(return_value=True)
    snapshot = analysis_worker.AdmissionSnapshot({"default": True})
    assert worker.dequeue_priority(snapshot)[0] is job
    assert worker.dequeue_priority(snapshot)[0] is job
    assert worker.codex_readiness.ready.call_args_list == [call(pending=True), call(pending=True)]


def test_priority_scan_batches_reads_and_resumes_after_its_wall_time_budget(
    monkeypatch, worker_factory, clock
):
    worker = priority_worker(monkeypatch, worker_factory)
    identifiers = [str(index) for index in range(50)]
    pages, batches, heartbeats = [], [], []

    def page(_key, start, end):
        pages.append((start, end))
        return identifiers[start : end + 1]

    def fetch(keys, **_kwargs):
        batches.append(list(keys))
        clock.now += 2
        return [queued_delivery(key, "article-analysis") for key in keys]

    def heartbeat():
        heartbeats.append(clock.now)
        worker.next_admission_heartbeat = clock.now + 3

    monkeypatch.setattr(worker.connection, "lrange", page)
    monkeypatch.setattr(worker.job_class, "fetch_many", fetch)
    worker.heartbeat = heartbeat
    assert [job.id for job in worker.priority_page(worker.queues[0])] == identifiers[:40]
    assert worker.scan_offsets == {"default": 40}
    assert [len(batch) for batch in batches] == [20, 20, 10]
    assert heartbeats == [0, 4]
    assert [job.id for job in worker.priority_page(worker.queues[0])] == identifiers[40:]
    assert pages == [(0, 99), (40, 139)]
    assert worker.scan_offsets == {"default": 0}


@pytest.mark.parametrize("stop_during", ["heartbeat", "fetch"])
def test_priority_scan_stops_before_yielding_or_claiming_after_a_slow_exchange(
    monkeypatch, worker_factory, stop_during
):
    worker = priority_worker(monkeypatch, worker_factory)
    monkeypatch.setattr(worker.connection, "lrange", lambda *_args: ["queued"])
    fetch = Mock(return_value=[queued_delivery("queued", "ingestion")])
    if stop_during == "heartbeat":
        worker.heartbeat = worker._shutdown
    else:

        def stopped_fetch(*_args, **_kwargs):
            worker._shutdown()
            return [queued_delivery("queued", "ingestion")]

        fetch.side_effect = stopped_fetch
    monkeypatch.setattr(worker.job_class, "fetch_many", fetch)
    claim = Mock()
    monkeypatch.setattr(worker.connection, "eval", claim)
    assert worker.dequeue_priority(analysis_worker.AdmissionSnapshot({"default": True})) is None
    claim.assert_not_called()
    if stop_during == "heartbeat":
        fetch.assert_not_called()


def test_priority_scan_removes_only_still_missing_hashes_and_stops_on_failed_heartbeat(
    monkeypatch, worker_factory
):
    worker = priority_worker(monkeypatch, worker_factory)
    monkeypatch.setattr(worker.connection, "lrange", lambda *_args: ["missing", "ready"])
    fetch = Mock(return_value=[None, queued_delivery("ready", "ingestion")])
    monkeypatch.setattr(worker.job_class, "fetch_many", fetch)
    cleanup = Mock(return_value=1)
    monkeypatch.setattr(worker.connection, "eval", cleanup)
    assert [job.id for job in worker.priority_page(worker.queues[0])] == ["ready"]
    cleanup.assert_called_once_with(
        analysis_worker.REMOVE_MISSING_DELIVERY,
        2,
        worker.queues[0].key,
        "rq:job:missing",
        "missing",
    )
    worker.heartbeat = Mock(side_effect=RedisError("unconfirmed heartbeat"))
    fetch.reset_mock()
    with pytest.raises(RedisError, match="unconfirmed heartbeat"):
        list(worker.priority_page(worker.queues[0]))
    fetch.assert_not_called()


@pytest.mark.parametrize("boundary", ["empty", "stopped", "budget"])
def test_priority_scan_rechecks_limits_before_reading_another_batch(
    monkeypatch, worker_factory, clock, boundary
):
    worker = priority_worker(monkeypatch, worker_factory)
    identifiers = [] if boundary == "empty" else [str(index) for index in range(40)]
    worker.scan_offsets["default"] = 0
    monkeypatch.setattr(worker.connection, "lrange", lambda *_args: identifiers)
    fetch = Mock(
        side_effect=lambda keys, **_kwargs: [queued_delivery(key, "ingestion") for key in keys]
    )
    monkeypatch.setattr(worker.job_class, "fetch_many", fetch)
    page = worker.priority_page(worker.queues[0])
    if boundary == "empty":
        assert list(page) == [] and worker.scan_offsets["default"] == 0
        fetch.assert_not_called()
        return
    assert [next(page).id for _ in range(20)] == identifiers[:20]
    if boundary == "stopped":
        worker._shutdown()
    else:
        clock.now = analysis_worker.ADMISSION_SCAN_SECONDS
    assert list(page) == []
    fetch.assert_called_once()
    assert worker.scan_offsets["default"] == 20


@pytest.mark.parametrize(
    "hint,expected", [(True, True), (False, False), (None, True), ("false", True)]
)
def test_partner_pipeline_admission_requires_explicit_transport_only_hint(hint, expected):
    job = SimpleNamespace(
        meta={"devfeed_queue": "partner-pipeline", "devfeed_ai": hint}, origin="default"
    )
    assert analysis_worker.job_requires_ai(job) is expected


def test_false_partner_hint_cannot_bypass_article_analysis_admission():
    job = SimpleNamespace(
        meta={"devfeed_queue": "article-analysis", "devfeed_ai": False}, origin="default"
    )
    assert analysis_worker.job_requires_ai(job)


def test_custom_partner_pipeline_consumer_has_readiness_for_ai_assessment(
    monkeypatch, worker_factory
):
    from devfeed_core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "job_queue_overrides", {"partner-pipeline": "partner-sync"})
    build, depths = worker_factory
    depths["partner-sync"] = 1
    worker = build("partner-sync")
    assert worker.codex_readiness is not None
    worker.codex_readiness.ready = Mock(return_value=True)
    job = SimpleNamespace(
        meta={"devfeed_queue": "partner-pipeline", "devfeed_ai": True}, origin="partner-sync"
    )
    assert worker.priority_admission(job, worker.queues[0], ai_blocked=False)
    worker.codex_readiness.ready.assert_called_once_with(pending=True)


@pytest.mark.parametrize("metadata", [None, [], "invalid-metadata"])
def test_invalid_metadata_shape_cannot_hide_a_valid_ai_handler(metadata):
    from devfeed_core.job_definitions import JOB_DEFINITIONS

    job = SimpleNamespace(
        meta=metadata, origin="default", func_name=JOB_DEFINITIONS["analysis"].handler
    )
    assert analysis_worker.job_requires_ai(job)


def test_failure_logging_does_not_decode_a_malformed_job_twice():
    from devfeed_aggregator.worker import job_fields
    from rq.exceptions import DeserializationError

    class MalformedJob:
        id = "failed-delivery"

        @property
        def args(self):
            raise DeserializationError()

    assert job_fields(MalformedJob()) == {
        "job_id": "failed-delivery",
        "rq_job_id": "failed-delivery",
        "job_kind": None,
    }


def test_dedicated_image_entrypoint_uses_its_custom_exclusive_route(monkeypatch):
    from devfeed_aggregator import worker
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "job_queue_overrides", {"images": "image-storage"})
    monkeypatch.setattr("sys.argv", ["devfeed-images-worker", "--burst"])
    start = Mock(return_value=0)
    monkeypatch.setattr(worker, "start_queue_worker", start)
    assert worker.queue_worker_main("images", "Images worker") == 0
    assert start.call_args.args[1] == "image-storage"


def test_dedicated_image_entrypoint_rejects_shared_transport(monkeypatch):
    from devfeed_aggregator import worker
    from devfeed_core.config import get_settings
    from devfeed_core.services import OperationConflict

    monkeypatch.setattr(get_settings(), "job_queue_overrides", {})
    monkeypatch.setattr("sys.argv", ["devfeed-source-discovery-worker", "--burst"])
    start = Mock()
    monkeypatch.setattr(worker, "start_queue_worker", start)
    with pytest.raises(OperationConflict, match="exclusive"):
        worker.queue_worker_main("source-discovery", "Discovery worker")
    start.assert_not_called()
