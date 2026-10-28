"""Finite import failures back off while shutdown and health remain bounded."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core import analytics_worker as worker


def test_schema_must_be_upgraded_before_historical_work(monkeypatch):
    client = object()
    schema = SimpleNamespace(
        check=AsyncMock(side_effect=worker.AnalyticsUnavailable("pending migration"))
    )
    importer = SimpleNamespace(once=AsyncMock())
    settings = SimpleNamespace(
        database_url="postgresql://fixture/devfeed_test",
        database_connect_timeout_seconds=5,
        analytics_backfill_batch_size=1000,
    )
    monkeypatch.setattr(worker, "ClickHouseMigrationRunner", lambda _: schema)
    monkeypatch.setattr(worker, "pipeline_policy_version", lambda _: "current")
    monkeypatch.setattr(worker, "AnalyticsImportRunner", lambda *_, **__: importer)
    service = worker.AnalyticsImportWorker(client, settings)
    with pytest.raises(worker.AnalyticsUnavailable, match="pending migration"):
        asyncio.run(service.once())
    schema.check.assert_awaited_once()
    importer.once.assert_not_awaited()


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


def test_single_cycle_propagates_outage_and_closes_client(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    importer = SimpleNamespace(once=AsyncMock(side_effect=RuntimeError("target unavailable")))
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "AnalyticsClient", lambda _: client)
    monkeypatch.setattr(worker, "AnalyticsImportWorker", lambda *_: importer)
    with pytest.raises(RuntimeError, match="target unavailable"):
        asyncio.run(worker.run(once=True))
    client.__aexit__.assert_awaited_once()


def test_backoff_deadline_returns_without_a_shutdown_signal():
    async def run():
        await asyncio.wait_for(worker.pause(asyncio.Event(), 0.001), timeout=0.1)

    asyncio.run(run())


@pytest.mark.parametrize("health", ["missing", "stale", "fresh"])
def test_health_probe_uses_only_the_owned_heartbeat(monkeypatch, tmp_path, health):
    import time

    marker = tmp_path / "analytics-heartbeat"
    if health != "missing":
        marker.write_text(str(time.time() if health == "fresh" else 0))
    monkeypatch.setattr(worker, "runtime_marker", lambda name: marker)
    monkeypatch.setattr("sys.argv", ["analytics-worker", "--check"])
    monkeypatch.setattr(
        worker, "get_settings", Mock(side_effect=AssertionError("probe opened clients"))
    )
    if health == "fresh":
        assert worker.main() is None
    else:
        with pytest.raises(SystemExit) as stopped:
            worker.main()
        assert stopped.value.code == 1


def test_shutdown_before_first_cycle_never_initializes_or_imports(monkeypatch):
    stopping = asyncio.Event()
    stopping.set()
    client = AsyncMock()
    client.__aenter__.return_value = client
    importer = SimpleNamespace(once=AsyncMock())
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "AnalyticsClient", lambda _: client)
    monkeypatch.setattr(worker, "AnalyticsImportWorker", lambda *_: importer)
    monkeypatch.setattr(worker.asyncio, "Event", lambda: stopping)
    asyncio.run(worker.run())
    importer.once.assert_not_called()
    client.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("busy", [False, True])
def test_single_successful_cycle_returns_without_waiting(monkeypatch, busy):
    client = AsyncMock()
    client.__aenter__.return_value = client
    importer = SimpleNamespace(once=AsyncMock(return_value=busy))
    pause = AsyncMock()
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "AnalyticsClient", lambda _: client)
    monkeypatch.setattr(worker, "AnalyticsImportWorker", lambda *_: importer)
    monkeypatch.setattr(worker, "pause", pause)
    asyncio.run(worker.run(once=True))
    importer.once.assert_awaited_once()
    pause.assert_not_called()
    client.__aexit__.assert_awaited_once()


def test_heartbeat_writer_publishes_a_current_timestamp_in_its_owned_marker(monkeypatch, tmp_path):
    import time

    marker = tmp_path / "analytics-heartbeat"
    monkeypatch.setattr(worker, "runtime_marker", lambda name: marker)
    before = time.time()
    worker.AnalyticsImportWorker._write_heartbeat()
    assert before <= float(marker.read_text()) <= time.time()
