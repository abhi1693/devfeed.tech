from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace

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
