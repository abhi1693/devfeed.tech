from types import SimpleNamespace

import pytest
from devfeed_aggregator import worker
from devfeed_core.config import get_settings


@pytest.mark.parametrize("available", [False, True])
def test_all_worker_requires_its_own_codex_socket_before_consuming_analysis(monkeypatch, available):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr(settings, "codex_app_server_url", "unix:///run/codex/app-server.sock")
    monkeypatch.setattr(worker.Path, "is_socket", lambda path: available)
    names = []

    def queue(name="ingestion"):
        names.append(name)
        return SimpleNamespace(connection=SimpleNamespace(close=lambda: None))

    monkeypatch.setattr(worker, "get_queue", queue)
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="worker", work=lambda **kw: None)
    )
    worker.run(burst=True)
    assert names == ["ingestion", *(["analysis"] if available else []), "notifications"]
    assert settings.ai_enabled


def test_dedicated_analysis_worker_fails_before_dequeueing_without_its_socket(monkeypatch):
    monkeypatch.setattr(get_settings(), "codex_app_server_url", "unix:///run/codex/missing.sock")
    monkeypatch.setattr(worker.Path, "is_socket", lambda path: False)
    monkeypatch.setattr(worker, "get_queue", lambda *a: pytest.fail("Opened an RQ queue"))
    with pytest.raises(RuntimeError, match="Codex socket unavailable"):
        worker.run(queue_name="analysis")
