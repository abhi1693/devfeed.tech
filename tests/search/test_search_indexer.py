from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_search_indexer import runtime


def test_once_processes_one_batch_and_reports_the_count(monkeypatch, tmp_path):
    output = []
    heartbeat = tmp_path / "heartbeat"
    target = tmp_path / "protected"
    target.write_text("unchanged")
    heartbeat.symlink_to(target)
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "background_cycle", lambda _: nullcontext())
    monkeypatch.setattr(runtime, "session_factory", lambda: object())
    monkeypatch.setattr(runtime, "sync_batch", lambda *_: 7)
    monkeypatch.setattr(runtime, "runtime_marker", lambda _: heartbeat)

    result = runtime._consume_index(Event(), object(), True, output.append)

    assert result == 0
    assert output == ['{"processed": 7}']
    assert heartbeat.is_file()
    assert not heartbeat.is_symlink()
    assert float(heartbeat.read_text()) > 0
    assert target.read_text() == "unchanged"


def test_once_returns_failure_without_claiming_success(monkeypatch):
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "background_cycle", lambda _: nullcontext())
    monkeypatch.setattr(runtime, "session_factory", lambda: object())

    def fail(*_):
        raise RuntimeError("index import failed")

    monkeypatch.setattr(runtime, "sync_batch", fail)
    output = []

    assert runtime._consume_index(Event(), object(), True, output.append) == 1
    assert output == []


@pytest.mark.parametrize("once", [False, True])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("configured_pool", [False, True])
def test_runtime_stops_telemetry_and_registers_shutdown_signals(
    monkeypatch, once, fails, queued_runtime_log, configured_pool
):
    engine, telemetry, stop = Mock(), object(), Mock()
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            log_format="json",
            search_indexer_connection_reuse_enabled=True,
            database_pool_enabled=configured_pool,
        ),
    )
    monkeypatch.setattr(runtime, "configure_logging", Mock())
    monkeypatch.setattr(runtime, "threading", SimpleNamespace(Event=lambda: stop))
    handlers = Mock()
    monkeypatch.setattr(runtime.signal, "signal", handlers)
    monkeypatch.setattr(runtime, "Typesense", lambda **_: engine)
    factory = SimpleNamespace(kw={"bind": Mock()})
    monkeypatch.setattr(runtime, "indexer_database", lambda _: nullcontext(factory))
    start, finish = Mock(return_value=telemetry), Mock()
    monkeypatch.setattr(runtime, "start_runtime", start)
    monkeypatch.setattr(runtime, "stop_runtime", finish)

    def consume(*args):
        assert args[-1] is (None if configured_pool else factory.kw["bind"].dispose)
        queued_runtime_log()
        if fails:
            raise RuntimeError("sync")
        return 0

    monkeypatch.setattr(runtime, "_consume_index", consume)
    if fails:
        with pytest.raises(RuntimeError):
            runtime.run_indexer(once=once)
    else:
        assert runtime.run_indexer(once=once) == 0
    engine.setup.assert_called_once()
    assert start.call_count == int(not once)
    finish.assert_called_once_with(None if once else telemetry)
    handlers.call_args.args[1](None, None)
    stop.set.assert_called_once()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("count", [0, 7])
def test_reconciliation_failure_does_not_stop_sync_and_interval_is_respected(
    monkeypatch, tmp_path, fails, count
):
    stop = Mock()
    stop.is_set.side_effect = [False, False, True]
    monkeypatch.setattr(
        runtime, "get_settings", lambda: SimpleNamespace(search_reconcile_interval_seconds=60)
    )
    monkeypatch.setattr(runtime, "current", lambda: object())
    monkeypatch.setattr(runtime.time, "monotonic", Mock(return_value=10))
    monkeypatch.setattr(runtime, "background_cycle", lambda _: nullcontext())
    monkeypatch.setattr(runtime, "session_factory", Mock())
    reconcile = Mock(side_effect=RuntimeError("reconcile")) if fails else Mock()
    monkeypatch.setattr(runtime, "reconcile", reconcile)
    sync = Mock(return_value=count)
    monkeypatch.setattr(runtime, "sync_batch", sync)
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(runtime, "runtime_marker", lambda _: heartbeat)
    assert runtime._consume_index(stop, object(), False, Mock()) == 0
    assert reconcile.call_count == 1 and sync.call_count == 2
    assert [call.args for call in stop.wait.call_args_list] == ([(1,), (1,)] if count == 0 else [])
    assert heartbeat.is_file()


def test_sync_failure_backs_off_without_writing_a_heartbeat(monkeypatch, tmp_path):
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "background_cycle", lambda _: nullcontext())
    monkeypatch.setattr(runtime, "session_factory", Mock())
    monkeypatch.setattr(runtime, "sync_batch", Mock(side_effect=RuntimeError("sync")))
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(runtime, "runtime_marker", lambda _: heartbeat)
    assert runtime._consume_index(stop, object(), False, Mock()) == 0
    stop.wait.assert_called_once_with(5)
    assert not heartbeat.exists()


def test_active_bursts_release_the_pool_before_idle_waits(monkeypatch, tmp_path):
    from threading import Event

    stop = Event()
    processed, releases = [], []
    counts = iter([10] * 25 + [0])

    def sync(*_):
        value = next(counts)
        processed.append(value)
        return value

    def release():
        releases.append(len(processed))

    def wait(seconds):
        assert seconds == 1 and releases == [20, 26]
        stop.set()

    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "sync_batch", sync)
    monkeypatch.setattr(runtime, "runtime_marker", lambda _: tmp_path / "heartbeat")
    monkeypatch.setattr(stop, "wait", wait)
    assert runtime._consume_index(stop, object(), False, Mock(), object(), release) == 0
    assert releases == [20, 26]


def test_dependency_failure_releases_the_pool_before_backoff(monkeypatch):
    from threading import Event

    stop, released = Event(), Mock()
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "sync_batch", Mock(side_effect=RuntimeError("dependency")))

    def wait(seconds):
        assert seconds == 5
        released.assert_called_once()
        stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    assert runtime._consume_index(stop, object(), False, Mock(), object(), released) == 0


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("configured_pool", [False, True])
def test_indexer_pool_is_owned_bounded_and_disposed_after_failure(
    monkeypatch, reuse, failed, configured_pool
):
    from devfeed_core.config import get_settings

    settings = get_settings().model_copy(
        update={
            "database_pool_enabled": configured_pool,
            "search_indexer_connection_reuse_enabled": reuse,
        }
    )
    database = Mock()
    build = Mock(return_value=database)
    maker = Mock(return_value=object())
    monkeypatch.setattr(runtime, "create_database_engine", build)
    monkeypatch.setattr(runtime, "sessionmaker", maker)
    result = pytest.raises(RuntimeError) if failed else nullcontext()
    with result, runtime.indexer_database(settings) as factory:
        assert factory is maker.return_value
        effective = build.call_args.args[0]
        assert effective.database_pool_enabled is (reuse or configured_pool)
        if reuse and not configured_pool:
            assert effective.database_pool_size == 1 and effective.database_max_overflow == 0
        else:
            assert effective is settings
        assert settings.database_pool_enabled is configured_pool
        if failed:
            raise RuntimeError("index failed")
    database.dispose.assert_called_once_with()
