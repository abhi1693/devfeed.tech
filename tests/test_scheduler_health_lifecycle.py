"""Exercise the heartbeat worker while scheduler cycles are still running."""

import threading
from contextlib import nullcontext
from functools import partial
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import quota_monitor, scheduler_health
from redis.exceptions import ConnectionError


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
