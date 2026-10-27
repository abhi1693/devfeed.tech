"""Finite import failures back off while shutdown and health remain bounded."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core import analytics_worker as worker


def test_schema_only_command_does_not_start_historical_work(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    status = SimpleNamespace(initialize=AsyncMock())
    runner = SimpleNamespace(prepare=AsyncMock())
    exporter = Mock()
    settings = SimpleNamespace(
        database_url="postgresql://fixture/devfeed_test",
        database_connect_timeout_seconds=5,
        analytics_backfill_batch_size=1000,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "AnalyticsClient", lambda _: client)
    monkeypatch.setattr(worker, "AnalyticsStatus", lambda _: status)
    monkeypatch.setattr(worker, "AnalyticsImportWorker", exporter)
    monkeypatch.setattr(worker, "AnalyticsImportRunner", lambda *_, **__: runner)
    asyncio.run(worker.run(initialize=True))
    client.initialize.assert_awaited_once()
    status.initialize.assert_awaited_once()
    runner.prepare.assert_awaited_once()
    exporter.assert_not_called()


def test_service_retries_failed_pages_with_backoff_and_closes_client(monkeypatch):
    class StopService(BaseException):
        pass

    client = AsyncMock()
    client.__aenter__.return_value = client
    exporter = SimpleNamespace(
        once=AsyncMock(side_effect=[RuntimeError("ClickHouse restarting"), True, False]),
        bootstrapping=True,
    )
    pause = AsyncMock(side_effect=[None, None, StopService()])
    settings = SimpleNamespace(analytics_poll_seconds=1, analytics_backfill_poll_seconds=0.1)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "AnalyticsClient", lambda _: client)
    monkeypatch.setattr(worker, "AnalyticsImportWorker", lambda *_: exporter)
    monkeypatch.setattr(worker, "pause", pause)
    with pytest.raises(StopService):
        asyncio.run(worker.run())
    assert [call.args[1] for call in pause.await_args_list] == [2, 0.1, 1]
    client.__aexit__.assert_awaited_once()


def test_cli_closes_telemetry_after_failure(monkeypatch):
    telemetry, finish = object(), Mock()
    settings = SimpleNamespace(log_level="INFO", log_format="json")
    monkeypatch.setattr("sys.argv", ["analytics-worker", "--once"])
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_logging", Mock())
    monkeypatch.setattr(worker, "start_runtime", lambda _: telemetry)
    monkeypatch.setattr(worker, "stop_runtime", finish)
    monkeypatch.setattr(worker, "run", AsyncMock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError, match="failed"):
        worker.main()
    finish.assert_called_once_with(telemetry)


def test_shutdown_interrupts_backoff_without_waiting_for_deadline():
    async def run():
        stopping = asyncio.Event()
        stopping.set()
        await asyncio.wait_for(worker.pause(stopping, 60), timeout=0.1)

    asyncio.run(run())
