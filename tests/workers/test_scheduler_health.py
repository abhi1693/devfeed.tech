"""Scheduler heartbeat and lifecycle cleanup contracts."""

import threading
from contextlib import nullcontext
from functools import partial
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import quota_monitor, scheduler_health
from devfeed_aggregator.scheduler_health import HEARTBEAT_KEY, SchedulerHealth
from redis.exceptions import ConnectionError


def test_long_cycles_keep_heartbeat_but_stalls_expire():
    clock = Mock(return_value=0)
    connection = Mock()
    health = SchedulerHealth(connection, clock)
    clock.return_value = 180
    health.pulse()
    assert connection.set.call_args.args[0] == HEARTBEAT_KEY
    assert connection.set.call_args.kwargs["ex"] == 120
    health.completed()
    clock.return_value = 350
    health.pulse()
    assert connection.set.call_count == 2
    clock.return_value = 481
    health.pulse()
    assert connection.set.call_count == 2


def test_heartbeat_recovers_from_redis_loss():
    connection = Mock()
    connection.set.side_effect = [ConnectionError(), True]
    health = SchedulerHealth(connection)
    health.pulse()
    health.pulse()
    assert connection.set.call_count == 2


class HeartbeatClock:
    """Advance worker time only after its previous heartbeat has finished."""

    def __init__(self):
        self.now = 0
        self.stopped = threading.Event()
        self.waiting = Queue()
        self.wake = Queue()

    def __call__(self):
        return self.now

    def is_set(self):
        return self.stopped.is_set()

    def wait(self, timeout):
        self.waiting.put(None)
        return self.wake.get(timeout=5)

    def set(self):
        self.stopped.set()
        self.wake.put(True)

    def heartbeat_finished(self):
        self.waiting.get(timeout=5)

    def advance_to(self, now):
        self.now = now
        self.wake.put(False)
        self.heartbeat_finished()


@pytest.fixture
def heartbeat(monkeypatch):
    clock = HeartbeatClock()
    connection = Mock()
    refresh = Mock()
    monkeypatch.setattr(scheduler_health, "create_redis", Mock(return_value=connection))
    monkeypatch.setattr(quota_monitor, "refresh_quota", refresh)
    monkeypatch.setattr(
        scheduler_health,
        "SchedulerHealth",
        partial(scheduler_health.SchedulerHealth, clock=clock),
    )
    # Keep the real background worker, but control its wait/clock without sleeping.
    monkeypatch.setattr(
        scheduler_health,
        "threading",
        SimpleNamespace(Event=lambda: clock, Thread=threading.Thread),
    )
    return SimpleNamespace(clock=clock, connection=connection, refresh=refresh)


def test_background_heartbeat_keeps_long_cycles_live_and_resumes_after_completion(heartbeat):
    connection, clock = heartbeat.connection, heartbeat.clock
    with scheduler_health.scheduler_health() as health:
        clock.heartbeat_finished()
        for now in (20, 60, 120, 180, 240, 299):
            clock.advance_to(now)
        assert connection.set.call_count == 7
        assert all(
            call.args[0] == scheduler_health.HEARTBEAT_KEY
            and call.kwargs["ex"] == scheduler_health.HEARTBEAT_TTL
            for call in connection.set.call_args_list
        )

        clock.advance_to(300)
        assert connection.set.call_count == 7
        health.completed()
        clock.advance_to(320)
        assert connection.set.call_count == 8


def test_background_quota_refresh_is_bounded_to_once_per_minute(heartbeat):
    clock, refresh = heartbeat.clock, heartbeat.refresh
    with scheduler_health.scheduler_health():
        clock.heartbeat_finished()
        refresh.assert_called_once_with()
        for now in (20, 40, 59):
            clock.advance_to(now)
        assert refresh.call_count == 1
        clock.advance_to(60)
        assert refresh.call_count == 2
        for now in (80, 100, 119):
            clock.advance_to(now)
        assert refresh.call_count == 2
        clock.advance_to(120)
        assert refresh.call_count == 3


@pytest.mark.parametrize("failed_cycle", [False, True])
def test_context_exit_stops_heartbeat_and_closes_redis(heartbeat, failed_cycle):
    outcome = pytest.raises(RuntimeError, match="cycle failed") if failed_cycle else nullcontext()
    with outcome, scheduler_health.scheduler_health():
        heartbeat.clock.heartbeat_finished()
        heartbeat.connection.close.assert_not_called()
        if failed_cycle:
            raise RuntimeError("cycle failed")

    assert heartbeat.clock.stopped.is_set()
    heartbeat.connection.close.assert_called_once_with()


def test_redis_failure_allows_scheduler_and_background_heartbeat_to_recover(heartbeat, caplog):
    heartbeat.connection.set.side_effect = [ConnectionError("redis unavailable"), True]
    with scheduler_health.scheduler_health() as health:
        heartbeat.clock.heartbeat_finished()
        health.completed()
        heartbeat.clock.advance_to(20)
        assert heartbeat.connection.set.call_count == 2
        heartbeat.refresh.assert_called_once_with()
        assert "scheduler_heartbeat_unavailable" in caplog.messages

    heartbeat.connection.close.assert_called_once_with()


def test_local_probe_tracks_redis_and_stall_health_without_importing_service_settings(tmp_path):
    from devfeed_aggregator.scheduler_probe import healthy

    marker = tmp_path / "heartbeat"
    clock = Mock(return_value=0)
    connection = Mock()
    health = SchedulerHealth(connection, clock, marker=marker)
    assert not healthy(marker)
    health.pulse()
    stamp = marker.stat().st_mtime
    assert healthy(marker, now=stamp)
    assert not healthy(marker, now=stamp - 1)
    assert not healthy(marker, now=stamp + 120)
    connection.set.side_effect = ConnectionError()
    health.pulse()
    assert not marker.exists()
    connection.set.side_effect = None
    clock.return_value = 300
    health.pulse()
    assert not marker.exists()
    health.completed()
    health.pulse()
    assert healthy(marker)


@pytest.mark.parametrize("operation", ["write", "remove"])
def test_local_marker_failure_is_logged_without_killing_heartbeat(
    tmp_path, monkeypatch, caplog, operation
):
    monkeypatch.setattr(
        scheduler_health, "write_runtime_marker", Mock(side_effect=OSError("read-only mount"))
    )
    connection = Mock()
    marker = tmp_path / "heartbeat"
    if operation == "remove":
        marker = Mock()
        marker.unlink.side_effect = OSError("read-only mount")
        connection.set.side_effect = ConnectionError()
    health = SchedulerHealth(connection, marker=marker)
    health.pulse()
    connection.set.assert_called_once()
    assert "scheduler_heartbeat_marker_unavailable" in caplog.messages


@pytest.mark.parametrize("exists", [False, True])
def test_probe_command_needs_no_database_or_redis_configuration(tmp_path, exists):
    import os
    import subprocess
    import sys

    marker = tmp_path / f"devfeed-runtime-{os.geteuid()}" / "scheduler-heartbeat"
    marker.parent.mkdir(mode=0o700)
    if exists:
        marker.write_text("healthy")
    result = subprocess.run(
        [sys.executable, "-m", "devfeed_aggregator.scheduler_probe"],
        env={
            **os.environ,
            "TMPDIR": str(tmp_path),
            "DEVFEED_DATABASE_URL": "invalid",
            "DEVFEED_REDIS_URL": "invalid",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (0 if exists else 1)
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("state,code", [("healthy", 0), ("missing", 1), ("unowned", 1)])
def test_probe_cli_reports_unavailable_local_health(tmp_path, monkeypatch, state, code):
    from devfeed_aggregator import scheduler_probe

    marker = tmp_path / "heartbeat"
    if state == "healthy":
        marker.write_text("healthy")
    if state == "unowned":
        monkeypatch.setattr(
            scheduler_probe, "runtime_marker", Mock(side_effect=PermissionError("unowned"))
        )
    else:
        monkeypatch.setattr(scheduler_probe, "runtime_marker", lambda _: marker)
    with pytest.raises(SystemExit) as failure:
        scheduler_probe.main()
    assert failure.value.code == code
