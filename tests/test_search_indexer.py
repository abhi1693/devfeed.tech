from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_search_indexer import runtime


def test_once_processes_one_batch_and_reports_the_count(monkeypatch, tmp_path):
    output = []
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "background_cycle", lambda _: nullcontext())
    monkeypatch.setattr(runtime, "session_factory", lambda: object())
    monkeypatch.setattr(runtime, "sync_batch", lambda *_: 7)
    monkeypatch.setattr(runtime, "Path", lambda _: heartbeat)

    result = runtime._consume_index(Event(), object(), True, output.append)

    assert result == 0
    assert output == ['{"processed": 7}']
    assert heartbeat.is_file()


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
def test_runtime_stops_telemetry_and_registers_shutdown_signals(monkeypatch, once, fails):
    engine, telemetry, stop = Mock(), object(), Mock()
    monkeypatch.setattr(
        runtime, "get_settings", lambda: SimpleNamespace(log_level="INFO", log_format="json")
    )
    monkeypatch.setattr(runtime, "configure_logging", Mock())
    monkeypatch.setattr(runtime.threading, "Event", lambda: stop)
    handlers = Mock()
    monkeypatch.setattr(runtime.signal, "signal", handlers)
    monkeypatch.setattr(runtime, "Typesense", lambda **_: engine)
    start, finish = Mock(return_value=telemetry), Mock()
    monkeypatch.setattr(runtime, "start_runtime", start)
    monkeypatch.setattr(runtime, "stop_runtime", finish)
    monkeypatch.setattr(
        runtime,
        "_consume_index",
        Mock(side_effect=RuntimeError("sync")) if fails else Mock(return_value=0),
    )
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
    monkeypatch.setattr(runtime, "Path", lambda _: heartbeat)
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
    monkeypatch.setattr(runtime, "Path", lambda _: heartbeat)
    assert runtime._consume_index(stop, object(), False, Mock()) == 0
    stop.wait.assert_called_once_with(5)
    assert not heartbeat.exists()
