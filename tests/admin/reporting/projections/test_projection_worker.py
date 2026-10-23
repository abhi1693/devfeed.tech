"""Publisher process controls, health signals and connection isolation without services."""

import os
import signal
import sys
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_admin_api import overview_projection
from devfeed_admin_api import projection_worker as worker
from devfeed_core import runtime_files
from devfeed_core.config import get_settings

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
    monkeypatch.setattr(worker, "drain_pipeline", drain)
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
    worker.run(drain=True)

    runtime.drain.assert_called_once_with(runtime.factory, batch=37)
    runtime.pipeline.assert_not_called()
    runtime.reports.assert_not_called()
    assert (runtime.directory / "pipeline-heartbeat").exists()
    assert runtime.waits == []
    runtime.engine.dispose.assert_called_once_with()
    runtime.finish.assert_called_once_with(runtime.telemetry)


@pytest.mark.parametrize("mode", ["once", "drain"])
def test_operator_run_propagates_failure_without_refreshing_health_and_cleans_up(runtime, mode):
    marker = runtime.directory / "pipeline-heartbeat"
    marker.touch()
    os.utime(marker, (1, 1))
    publisher = runtime.pipeline if mode == "once" else runtime.drain
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
@pytest.mark.parametrize(("count", "age"), [(0, None), (12, 93), (3, -10)])
def test_backlog_metrics_report_pending_work_and_nonnegative_oldest_age(
    monkeypatch, kind, count, age
):
    pending, oldest = Mock(), Mock()
    runtime = NS(instruments={"read_model_pending": pending, "read_model_oldest": oldest})
    session = Mock()
    session.execute.return_value.one.return_value = (
        count,
        NOW - timedelta(seconds=age) if age is not None else None,
    )
    factory = Mock(return_value=nullcontext(session))
    monkeypatch.setattr(worker, "current", lambda: runtime)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)

    worker.record_backlog(factory, kind)

    pending.set.assert_called_once_with(count, {"kind": kind})
    oldest.set.assert_called_once_with(max(0, age or 0), {"kind": kind})


def test_disabled_telemetry_does_not_open_a_backlog_connection(monkeypatch):
    monkeypatch.setattr(worker, "current", lambda: None)
    factory = Mock(side_effect=AssertionError("Telemetry is disabled"))

    worker.record_backlog(factory, "pipeline")

    factory.assert_not_called()
