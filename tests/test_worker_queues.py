from types import SimpleNamespace

import pytest
from devfeed_aggregator import worker
from devfeed_core.config import get_settings


@pytest.mark.parametrize("queue_name", ["all", "analysis"])
def test_workers_keep_analysis_configured_and_check_readiness_when_dequeueing(
    monkeypatch, queue_name
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr(settings, "codex_app_server_url", "unix:///run/codex/app-server.sock")
    names = []

    def queue(name="ingestion"):
        names.append(name)
        return SimpleNamespace(connection=SimpleNamespace(close=lambda: None))

    monkeypatch.setattr(worker, "get_queue", queue)
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="worker", work=lambda **kw: None)
    )
    worker.run(burst=True, queue_name=queue_name)
    assert names == (
        ["ingestion", "analysis", "relationships", "notifications"]
        if queue_name == "all"
        else ["analysis", "relationships"]
    )
    assert settings.ai_enabled


def test_readiness_backs_off_during_outage_and_checks_each_available_job(monkeypatch):
    from devfeed_aggregator import analysis_worker

    clock, calls = [0.0], []
    reasons = iter([None, "codex_unavailable", None, None])

    def check():
        calls.append(clock[0])
        return next(reasons)

    monkeypatch.setattr(analysis_worker, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    readiness = analysis_worker.CodexReadiness()
    readiness.client = SimpleNamespace(readiness=check)
    assert readiness.ready(pending=False)
    clock[0] = 1
    assert readiness.ready(pending=False)  # Idle can reuse the recent successful probe.
    assert not readiness.ready(pending=True)  # Pending work requires a fresh probe.
    for tick in range(2, 11):
        clock[0] = tick
        assert not readiness.ready(pending=True)
    clock[0] = 11
    assert readiness.ready(pending=True)
    assert readiness.ready(pending=True)
    assert calls == [0, 1, 11, 11]
