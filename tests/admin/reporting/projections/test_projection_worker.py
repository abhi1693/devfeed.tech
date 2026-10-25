"""Publisher process controls, health signals and connection isolation without services."""

import os
import runpy
import signal
import sys
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock, call

import pytest
from devfeed_admin_api import overview_projection
from devfeed_admin_api import projection_worker as worker
from devfeed_core import db, reporting_facts, runtime_files
from devfeed_core.config import get_settings
from devfeed_core.models import ReadModelState

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


@pytest.fixture
def heartbeat_directory(monkeypatch, tmp_path):
    """Use real marker files while keeping the CLI's paths isolated from other workers."""
    monkeypatch.setattr(runtime_files, "gettempdir", lambda: str(tmp_path))
    return worker.heartbeat_marker("pipeline").parent


@pytest.mark.parametrize("kind", ["pipeline", "reports"])
@pytest.mark.parametrize("age", [None, 181, 180, 10])
def test_healthcheck_requires_a_recent_heartbeat_for_the_selected_kind(
    monkeypatch, heartbeat_directory, kind, age
):
    now = NOW.timestamp()
    other = "reports" if kind == "pipeline" else "pipeline"
    (heartbeat_directory / f"{other}-heartbeat").touch()
    marker = heartbeat_directory / f"{kind}-heartbeat"
    if age is not None:
        marker.touch()
        os.utime(marker, (now - age, now - age))
    monkeypatch.setattr(worker.time, "time", lambda: now)
    monkeypatch.setattr(sys, "argv", ["projection-worker", "--healthcheck", "--kind", kind])
    publish = Mock(side_effect=AssertionError("A health check must not start a publisher"))
    monkeypatch.setattr(worker, "run", publish)

    if age is None or age > 180:
        with pytest.raises(SystemExit) as failure:
            worker.main()
        assert failure.value.code == 1
    else:
        worker.main()
    publish.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], {"once": False, "drain": False, "kind": "pipeline"}),
        (["--once", "--kind", "reports"], {"once": True, "drain": False, "kind": "reports"}),
        (["--drain"], {"once": False, "drain": True, "kind": "pipeline"}),
    ],
)
def test_cli_selects_the_requested_publisher_and_run_mode(monkeypatch, arguments, expected):
    publish = Mock()
    monkeypatch.setattr(worker, "run", publish)
    monkeypatch.setattr(sys, "argv", ["projection-worker", *arguments])
    worker.main()
    publish.assert_called_once_with(**expected)


def test_cli_rejects_an_unknown_publisher_before_starting_services(monkeypatch):
    publish = Mock()
    monkeypatch.setattr(worker, "run", publish)
    monkeypatch.setattr(sys, "argv", ["projection-worker", "--kind", "unknown"])
    with pytest.raises(SystemExit) as failure:
        worker.main()
    assert failure.value.code == 2
    publish.assert_not_called()


def test_module_entrypoint_runs_the_selected_healthcheck_without_starting_services(
    monkeypatch, heartbeat_directory
):
    marker = worker.heartbeat_marker("reports")
    marker.touch()
    monkeypatch.setattr(sys, "argv", ["projection-worker", "--healthcheck", "--kind", "reports"])
    create_engine = Mock(side_effect=AssertionError("A health check opened the database"))
    monkeypatch.setattr(db, "create_database_engine", create_engine)
    runpy.run_path(worker.__file__, run_name="__main__")
    create_engine.assert_not_called()
    assert list(heartbeat_directory.iterdir()) == [marker]


@pytest.mark.parametrize(
    "pending,coverage,complete",
    [
        (None, {"backfill_complete": True}, True),
        (41, {"backfill_complete": True}, False),
        (None, None, False),
        (None, {}, False),
        (None, {"backfill_complete": False}, False),
    ],
)
def test_pipeline_completion_requires_both_finished_coverage_and_an_empty_outbox(
    pending, coverage, complete
):
    session = Mock()
    session.get.return_value = (
        ReadModelState(name="pipeline", payload=coverage) if coverage is not None else None
    )
    session.scalar.return_value = pending
    factory = Mock(return_value=nullcontext(session))
    if complete:
        assert worker.pipeline_drained(factory)
    else:
        with pytest.raises(RuntimeError, match="busy or its publisher lease expired"):
            worker.pipeline_drained(factory)
    session.get.assert_called_once_with(ReadModelState, "pipeline")
    statement = session.scalar.call_args.args[0]
    assert statement._limit == 1
    assert "read_model_events" in str(statement)
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "case,expected",
    [
        ("ready", True),
        ("backfilling", False),
        ("unclaimed", False),
        ("dependency_cooldown", False),
        ("expired_lease", False),
        ("active_lease", "publication is busy"),
        ("failed_cooldown", "daily/90: TimeoutError"),
        ("database_failure", "Fact database unavailable"),
    ],
)
def test_report_completion_preserves_retry_ownership_and_requires_ready_facts(
    monkeypatch, case, expected
):
    queued = case not in {"ready", "backfilling", "database_failure"}
    pending = (
        NS(
            group="daily",
            days=90,
            lease_until=(
                NOW + timedelta(seconds=45)
                if case == "active_lease"
                else NOW - timedelta(seconds=1)
                if case == "expired_lease"
                else None
            ),
            available_at=NOW + timedelta(seconds=15),
            last_error=(
                "ProjectionNotReady"
                if case == "dependency_cooldown"
                else "TimeoutError"
                if case == "failed_cooldown"
                else None
            ),
        )
        if queued
        else None
    )
    session = Mock()
    session.scalar.return_value = pending
    require = Mock()
    if case == "backfilling":
        require.side_effect = reporting_facts.ReportingFactsUnavailable("Bootstrap incomplete")
    elif case == "database_failure":
        require.side_effect = RuntimeError("Fact database unavailable")
    monkeypatch.setattr(reporting_facts, "require_reporting_facts", require)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    factory = Mock(return_value=nullcontext(session))
    before = vars(pending).copy() if pending else None
    if isinstance(expected, str):
        with pytest.raises(RuntimeError, match=expected):
            worker.reports_drained(factory)
    else:
        assert worker.reports_drained(factory) is expected
    if case in {"active_lease", "failed_cooldown"}:
        require.assert_not_called()
    else:
        require.assert_called_once_with(session)
    if pending:
        assert vars(pending) == before
    statement = session.scalar.call_args.args[0]
    assert statement._limit == 1
    assert "report_refreshes.available_at" in str(statement)
    session.execute.assert_not_called()
    session.delete.assert_not_called()


@pytest.mark.parametrize("kind", ["../outside", "reports/../../outside", "unknown"])
def test_run_rejects_unknown_publishers_before_starting_services(monkeypatch, kind):
    configure = Mock()
    monkeypatch.setattr(worker, "get_settings", configure)

    with pytest.raises(ValueError, match="Unknown read-model publisher"):
        worker.run(once=True, kind=kind)

    configure.assert_not_called()


@pytest.fixture
def runtime(monkeypatch, heartbeat_directory):
    settings = get_settings().model_copy(
        update={
            "database_url": "postgresql+psycopg://interactive@database.invalid/app",
            "read_model_database_url": "postgresql+psycopg://publisher@database.invalid/app",
            "database_pool_enabled": True,
            "read_model_batch_size": 37,
            "read_model_poll_seconds": 0.25,
        }
    )
    engine, factory, telemetry = Mock(), Mock(), object()
    create_engine = Mock(return_value=engine)
    make_sessions = Mock(return_value=factory)
    start, finish = Mock(return_value=telemetry), Mock()
    pipeline, drain, reports = Mock(return_value=3), Mock(return_value=20), Mock(return_value=1)
    backlog = Mock()
    handlers, cycles, waits = {}, [], []
    state = NS(stopped=False, on_wait=lambda: None)

    def wait(seconds):
        waits.append(seconds)
        state.on_wait()

    stopping = NS(
        is_set=lambda: state.stopped, set=lambda: setattr(state, "stopped", True), wait=wait
    )

    @contextmanager
    def cycle(name):
        cycles.append(("started", name))
        try:
            yield
        finally:
            cycles.append(("finished", name))

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_logging", Mock())
    monkeypatch.setattr(worker, "start_runtime", start)
    monkeypatch.setattr(worker, "stop_runtime", finish)
    monkeypatch.setattr(worker, "create_database_engine", create_engine)
    monkeypatch.setattr(worker, "sessionmaker", make_sessions)
    monkeypatch.setattr(worker, "threading", NS(Event=lambda: stopping))
    monkeypatch.setattr(worker, "signal", NS(SIGTERM=signal.SIGTERM, SIGINT=signal.SIGINT))
    monkeypatch.setattr(
        worker.signal,
        "signal",
        lambda name, handler: handlers.update({name: handler}),
        raising=False,
    )
    monkeypatch.setattr(worker, "background_cycle", cycle)
    monkeypatch.setattr(worker, "project_batch", pipeline)
    drained = Mock(return_value=True)
    monkeypatch.setattr(worker, "pipeline_drained", drained)
    report_drain = Mock(return_value=True)
    monkeypatch.setattr(worker, "reports_drained", report_drain)
    monkeypatch.setattr(overview_projection, "refresh_overview_projections", reports)
    monkeypatch.setattr(worker, "record_backlog", backlog)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    return NS(
        settings=settings,
        engine=engine,
        factory=factory,
        telemetry=telemetry,
        create_engine=create_engine,
        make_sessions=make_sessions,
        start=start,
        finish=finish,
        pipeline=pipeline,
        drain=drain,
        drained=drained,
        report_drain=report_drain,
        reports=reports,
        backlog=backlog,
        handlers=handlers,
        cycles=cycles,
        waits=waits,
        state=state,
        directory=heartbeat_directory,
    )


@pytest.mark.parametrize("dedicated", [False, True])
def test_pipeline_once_uses_its_own_unpooled_connection_settings_and_cleans_up(runtime, dedicated):
    if not dedicated:
        runtime.settings.read_model_database_url = None
    interactive_url = runtime.settings.database_url

    worker.run(once=True)

    config = runtime.create_engine.call_args.args[0]
    assert config.database_url == (
        runtime.settings.read_model_database_url if dedicated else interactive_url
    )
    assert config.database_pool_enabled is False
    assert config.database_application_name == "devfeed-read-model-pipeline"
    assert runtime.settings.database_pool_enabled is True
    assert runtime.settings.database_url == interactive_url
    runtime.make_sessions.assert_called_once_with(runtime.engine, expire_on_commit=False)
    runtime.pipeline.assert_called_once_with(runtime.factory, batch=37)
    runtime.drain.assert_not_called()
    runtime.reports.assert_not_called()
    runtime.backlog.assert_called_once_with(runtime.factory, "pipeline")
    assert (runtime.directory / "pipeline-heartbeat").exists()
    assert not (runtime.directory / "reports-heartbeat").exists()
    assert runtime.waits == []
    assert runtime.cycles == [
        ("started", "read-models.pipeline"),
        ("finished", "read-models.pipeline"),
    ]
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


def test_report_once_does_not_run_the_pipeline_publisher(runtime):
    worker.run(once=True, kind="reports")

    runtime.reports.assert_called_once_with(runtime.factory, limit=1, now=NOW)
    runtime.pipeline.assert_not_called()
    runtime.drain.assert_not_called()
    runtime.backlog.assert_called_once_with(runtime.factory, "reports")
    assert (runtime.directory / "reports-heartbeat").exists()
    assert not (runtime.directory / "pipeline-heartbeat").exists()
    assert runtime.waits == []
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


@pytest.mark.parametrize("kind", ["pipeline", "reports"])
@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_successful_cycle_replaces_heartbeat_links_without_touching_their_target(
    runtime, tmp_path, kind, link
):
    target = tmp_path / "protected"
    target.write_text("keep this content")
    os.utime(target, (1, 1))
    marker = runtime.directory / f"{kind}-heartbeat"
    if link == "symbolic":
        marker.symlink_to(target)
    else:
        marker.hardlink_to(target)

    worker.run(once=True, kind=kind)

    assert target.read_text() == "keep this content"
    assert target.stat().st_mtime == 1
    assert not marker.is_symlink()
    assert marker.stat().st_ino != target.stat().st_ino
    assert marker.stat().st_mode & 0o777 == 0o600
    assert float(marker.read_text()) > 1
    assert list(runtime.directory.iterdir()) == [marker]


@pytest.mark.parametrize("kind", ["pipeline", "reports"])
@pytest.mark.parametrize("healthcheck", [False, True])
def test_worker_rejects_a_public_runtime_directory_before_use(
    monkeypatch, heartbeat_directory, kind, healthcheck
):
    heartbeat_directory.chmod(0o777)
    monkeypatch.setattr(
        sys,
        "argv",
        ["projection-worker", "--kind", kind, *(["--healthcheck"] if healthcheck else ["--once"])],
    )

    with pytest.raises(PermissionError, match="owned private directory"):
        worker.main()

    assert not list(heartbeat_directory.iterdir())


def test_drain_finishes_backfill_instead_of_one_batch(runtime):
    runtime.pipeline.side_effect = [20, 0]
    worker.run(drain=True)

    assert runtime.pipeline.call_count == 2
    runtime.drained.assert_called_once_with(runtime.factory)
    runtime.drain.assert_not_called()
    runtime.reports.assert_not_called()
    assert (runtime.directory / "pipeline-heartbeat").exists()
    assert runtime.waits == [0.25]
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


def test_report_drain_waits_for_fact_readiness_and_requested_publications(runtime):
    runtime.reports.return_value = 0
    runtime.report_drain.side_effect = [False, False, True]
    worker.run(drain=True, kind="reports")
    assert runtime.reports.call_count == 3
    assert runtime.reports.call_args_list[0].kwargs == {
        "limit": 1,
        "now": NOW,
        "seed": True,
        "fail_fast": True,
    }
    assert all(
        invocation.kwargs == {"limit": 1, "now": NOW, "seed": False, "fail_fast": True}
        for invocation in runtime.reports.call_args_list[1:]
    )
    assert runtime.report_drain.call_count == 3
    assert runtime.waits == [0.25, 0.25]
    assert (runtime.directory / "reports-heartbeat").exists()


def test_report_drain_fails_explicitly_when_work_never_converges(runtime, monkeypatch):
    monkeypatch.setattr(worker, "MAX_DRAIN_BATCHES", 2)
    runtime.report_drain.return_value = False
    with pytest.raises(RuntimeError, match="Reports projection has not drained"):
        worker.run(drain=True, kind="reports")
    assert runtime.reports.call_count == 2
    assert runtime.waits == [0.25]


def test_report_drain_propagates_publisher_failures_without_sleeping(runtime):
    runtime.reports.side_effect = RuntimeError("Fact backfill failed")
    with pytest.raises(RuntimeError, match="Fact backfill failed"):
        worker.run(drain=True, kind="reports")
    assert runtime.waits == []
    assert not (runtime.directory / "reports-heartbeat").exists()


@pytest.mark.parametrize("mode", ["once", "drain"])
def test_operator_run_propagates_failure_without_refreshing_health_and_cleans_up(runtime, mode):
    marker = runtime.directory / "pipeline-heartbeat"
    marker.touch()
    os.utime(marker, (1, 1))
    publisher = runtime.pipeline
    publisher.side_effect = RuntimeError("Publisher lease expired")

    with pytest.raises(RuntimeError, match="Publisher lease expired"):
        worker.run(**{mode: True})

    assert marker.stat().st_mtime == 1
    runtime.backlog.assert_not_called()
    runtime.reports.assert_not_called()
    assert runtime.waits == []
    assert runtime.cycles[-1] == ("finished", "read-models.pipeline")
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


@pytest.mark.parametrize("kind,duty", [("pipeline", 0.5), ("reports", 0.25)])
def test_service_pays_for_sustained_work_and_keeps_heartbeats_during_long_yields(
    runtime, monkeypatch, kind, duty
):
    clock = {"now": 0.0}
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(worker.time, "time", lambda: clock["now"])
    runtime.settings.read_model_pipeline_duty_cycle = duty
    runtime.settings.read_model_report_duty_cycle = duty
    publisher = runtime.pipeline if kind == "pipeline" else runtime.reports

    def publish(*args, **kwargs):
        clock["now"] += 120
        return 1

    publisher.side_effect = publish
    marker = runtime.directory / f"{kind}-heartbeat"
    seen = []
    expected_wait = 120 * (1 / duty - 1)

    def on_wait():
        seen.append(float(marker.read_text()))
        clock["now"] += runtime.waits[-1]
        if sum(runtime.waits) >= expected_wait:
            runtime.state.stopped = True

    runtime.state.on_wait = on_wait
    worker.run(kind=kind)

    assert sum(runtime.waits) == expected_wait
    assert max(runtime.waits) <= 30
    assert seen == [120 + 30 * index for index in range(len(seen))]
    publisher.assert_called_once()


def test_failed_work_also_pays_its_duty_budget_without_publishing_a_healthy_marker(
    runtime, monkeypatch
):
    clock = {"now": 0.0}
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["now"])
    runtime.settings.read_model_pipeline_duty_cycle = 0.5

    def fail(*args, **kwargs):
        clock["now"] = 10
        raise RuntimeError("Temporary database failure")

    runtime.pipeline.side_effect = fail
    runtime.state.on_wait = lambda: setattr(runtime.state, "stopped", True)
    worker.run()
    assert runtime.waits == [10]
    assert not list(runtime.directory.iterdir())


def test_pipeline_adapts_slow_and_failed_batches_then_recovers_productive_throughput(
    runtime, monkeypatch
):
    clock = {"now": 0.0}
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["now"])
    outcomes = iter([(40, True), (40, False), (1, False), (15, False), (1, False)])
    runtime.settings.read_model_pipeline_duty_cycle = 0.5

    def publish(*args, **kwargs):
        duration, fails = next(outcomes)
        clock["now"] += duration
        if fails:
            raise RuntimeError("Publisher lease expired during a slow batch")
        return 3

    def on_wait():
        clock["now"] += runtime.waits[-1]
        if runtime.pipeline.call_count == 5:
            runtime.state.stopped = True

    runtime.pipeline.side_effect = publish
    runtime.state.on_wait = on_wait
    worker.run()
    assert [invocation.kwargs["batch"] for invocation in runtime.pipeline.call_args_list] == [
        37,
        18,
        9,
        10,
        10,
    ]
    assert sum(runtime.waits) == 97
    assert max(runtime.waits) <= 30


@pytest.mark.parametrize("maximum,expected", [(1, [1, 1, 1]), (203, [200, 203, 203])])
def test_pipeline_adaptation_preserves_configured_bounds_and_a_safe_start(
    runtime, monkeypatch, maximum, expected
):
    clock = {"now": 0.0}
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["now"])
    runtime.settings.read_model_batch_size = maximum

    def publish(*args, **kwargs):
        clock["now"] += 1
        return 1

    def on_wait():
        clock["now"] += runtime.waits[-1]
        if runtime.pipeline.call_count == 3:
            runtime.state.stopped = True

    runtime.pipeline.side_effect = publish
    runtime.state.on_wait = on_wait
    worker.run()
    assert [
        invocation.kwargs["batch"] for invocation in runtime.pipeline.call_args_list
    ] == expected


def test_drain_rejects_no_progress_when_a_publisher_is_busy(runtime):
    runtime.pipeline.return_value = 0
    runtime.drained.side_effect = RuntimeError("Pipeline backfill is busy")
    with pytest.raises(RuntimeError, match="backfill is busy"):
        worker.run(drain=True)
    assert not list(runtime.directory.iterdir())
    assert runtime.waits == []


@pytest.mark.parametrize("kind", ["pipeline", "reports"])
@pytest.mark.parametrize("shutdown_signal", [signal.SIGTERM, signal.SIGINT])
def test_service_retries_failed_cycles_and_stops_on_a_shutdown_signal(
    runtime, kind, shutdown_signal, caplog
):
    publisher = runtime.pipeline if kind == "pipeline" else runtime.reports
    publisher.side_effect = [RuntimeError("Temporary database failure"), 1]
    marker = runtime.directory / f"{kind}-heartbeat"
    observations = []

    def on_wait():
        observations.append(marker.exists())
        if len(observations) == 2:
            runtime.handlers[shutdown_signal](shutdown_signal, None)

    runtime.state.on_wait = on_wait
    worker.run(kind=kind)

    assert publisher.call_count == 2
    assert observations == [False, True]
    assert runtime.waits == [0.25, 0.25]
    assert runtime.state.stopped
    assert "read_model_cycle_failed" in caplog.text
    runtime.backlog.assert_called_once_with(runtime.factory, kind)
    assert runtime.cycles == [
        ("started", f"read-models.{kind}"),
        ("finished", f"read-models.{kind}"),
        ("started", f"read-models.{kind}"),
        ("finished", f"read-models.{kind}"),
    ]
    (runtime.reports if kind == "pipeline" else runtime.pipeline).assert_not_called()
    runtime.drain.assert_not_called()
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


def test_backlog_failure_does_not_advertise_a_successful_heartbeat(runtime):
    runtime.backlog.side_effect = RuntimeError("Backlog database unavailable")
    with pytest.raises(RuntimeError, match="Backlog database unavailable"):
        worker.run(once=True)

    runtime.pipeline.assert_called_once()
    assert not list(runtime.directory.iterdir())
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


@pytest.mark.parametrize("kind", ["pipeline", "reports"])
def test_queue_metrics_are_sampled_without_delaying_publication_or_heartbeats(
    runtime, monkeypatch, kind
):
    ticks = iter([0, 10, 29, 30, 60])
    clock = {"now": next(ticks)}
    samples, heartbeats = [], []
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(worker.time, "time", lambda: clock["now"])
    runtime.backlog.side_effect = lambda *_: samples.append(clock["now"])
    marker = runtime.directory / f"{kind}-heartbeat"

    def on_wait():
        heartbeats.append(float(marker.read_text()))
        if len(heartbeats) == 5:
            runtime.state.stopped = True
        else:
            clock["now"] = next(ticks)

    runtime.state.on_wait = on_wait
    worker.run(kind=kind)

    publisher = runtime.pipeline if kind == "pipeline" else runtime.reports
    assert publisher.call_count == 5
    assert samples == [0, 30, 60]
    assert heartbeats == [0, 10, 29, 30, 60]


@pytest.mark.parametrize("kind", ["pipeline", "reports"])
@pytest.mark.parametrize(("count", "age"), [(0, None), (12, 93), (3, -10)])
def test_backlog_metrics_report_pending_work_and_nonnegative_oldest_age(
    monkeypatch, kind, count, age
):
    pending, oldest = Mock(), Mock()
    runtime = NS(instruments={"read_model_pending": pending, "read_model_oldest": oldest})
    session = Mock()
    results = [NS(one=lambda: (count, NOW - timedelta(seconds=age) if age is not None else None))]
    if kind == "reports":
        results.append(NS(one=lambda: (7, NOW - timedelta(seconds=11))))
    session.execute.side_effect = results
    factory = Mock(return_value=nullcontext(session))
    monkeypatch.setattr(worker, "current", lambda: runtime)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)

    worker.record_backlog(factory, kind)

    pending_calls = [call(count, {"kind": kind})]
    oldest_calls = [call(max(0, age or 0), {"kind": kind})]
    if kind == "reports":
        # Dirty facts remain visible even when no report document is queued.
        pending_calls.append(call(7, {"kind": "reporting-facts"}))
        oldest_calls.append(call(11, {"kind": "reporting-facts"}))
    assert pending.set.call_args_list == pending_calls
    assert oldest.set.call_args_list == oldest_calls
    assert session.execute.call_count == (2 if kind == "reports" else 1)


def test_disabled_telemetry_does_not_open_a_backlog_connection(monkeypatch):
    monkeypatch.setattr(worker, "current", lambda: None)
    factory = Mock(side_effect=AssertionError("Telemetry is disabled"))

    worker.record_backlog(factory, "pipeline")

    factory.assert_not_called()
