"""The direct analytics worker finishes history once and never polls sources again."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core import analytics_worker as worker


@pytest.fixture
def service(monkeypatch):
    settings = SimpleNamespace(
        database_url="postgresql://fixture/fixture_test",
        database_connect_timeout_seconds=5,
        analytics_backfill_batch_size=100,
    )
    client = SimpleNamespace(initialize=AsyncMock())
    status = SimpleNamespace(initialize=AsyncMock(), read=AsyncMock(), write=AsyncMock())
    importer = SimpleNamespace(
        cutover=datetime(2026, 10, 1, tzinfo=UTC),
    )
    runner = SimpleNamespace(once=AsyncMock(), _importer=importer)
    monkeypatch.setattr(worker, "AnalyticsStatus", lambda _: status)
    monkeypatch.setattr(worker, "AnalyticsImportRunner", lambda *_, **__: runner)
    monkeypatch.setattr(worker, "pipeline_policy_version", lambda _: "current")
    monkeypatch.setattr(worker.AnalyticsImportWorker, "_write_heartbeat", Mock())
    return worker.AnalyticsImportWorker(client, settings), client, status, runner


def test_ready_restart_and_idle_cycles_never_read_postgres(service):
    exporter, client, status, runner = service
    status.read.return_value = {"ready": True, "policy": "current"}

    async def run():
        assert await exporter.once() is False
        assert await exporter.once() is False

    asyncio.run(run())
    runner.once.assert_not_called()
    status.write.assert_not_called()
    client.initialize.assert_awaited_once()
    assert exporter.bootstrapping is False


def test_old_policy_fails_closed_until_its_card_pages_and_cleanup_complete(service):
    exporter, _, status, runner = service
    cutoff = runner._importer.cutover
    status.read.return_value = {"ready": True, "policy": "old", "capture_started_at": cutoff}
    runner.once.side_effect = [worker.ImportStep.PROGRESSED, worker.ImportStep.COMPLETE]

    async def run():
        assert await exporter.once() is True
        assert exporter.bootstrapping is True
        assert await exporter.once() is False
        assert await exporter.once() is False

    asyncio.run(run())
    assert status.write.await_args_list[0].kwargs == {
        "ready": False,
        "cutover": cutoff,
        "policy": "current",
    }
    assert status.write.await_args_list[1].kwargs == {
        "ready": True,
        "cutover": cutoff,
        "policy": "current",
    }
    assert runner.once.await_count == 2


def test_failed_page_never_publishes_readiness_or_heartbeat(service):
    exporter, _, status, runner = service
    status.read.side_effect = worker.AnalyticsUnavailable("not imported")
    runner.once.side_effect = RuntimeError("ClickHouse unavailable")
    with pytest.raises(RuntimeError, match="ClickHouse unavailable"):
        asyncio.run(exporter.once())
    assert exporter.bootstrapping is True
    assert status.write.await_args.kwargs["ready"] is False
    exporter._write_heartbeat.assert_not_called()
