"""Exporter outages retain delivery retries and observable bounded queue age."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core import analytics_worker as worker


class StopService(BaseException):
    pass


@pytest.fixture
def service(monkeypatch):
    settings = NS(
        database_url="postgresql+psycopg://unused/devfeed_test",
        database_connect_timeout_seconds=3,
        analytics_poll_seconds=0.25,
        analytics_backfill_poll_seconds=0.5,
        log_level="INFO",
        log_format="json",
    )

    class Client:
        initialize = AsyncMock()
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

    client = Client()
    client.initialize = AsyncMock()
    exporter = NS(once=AsyncMock(return_value=False), bootstrapping=True)
    make_exporter = Mock(return_value=exporter)
    backlog, sleep = AsyncMock(), AsyncMock()
    cycles = []

    @contextmanager
    def cycle(name):
        cycles.append(name)
        yield

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "AnalyticsClient", lambda _settings: client)
    monkeypatch.setattr(worker, "AnalyticsExporter", make_exporter)
    monkeypatch.setattr(worker, "record_backlog", backlog)
    monkeypatch.setattr(worker, "background_cycle", cycle)
    monkeypatch.setattr(worker, "asyncio", NS(sleep=sleep))
    monkeypatch.setattr(worker, "time", NS(monotonic=lambda: 0.0))
    return NS(
        settings=settings,
        client=client,
        exporter=exporter,
        make_exporter=make_exporter,
        backlog=backlog,
        sleep=sleep,
        cycles=cycles,
    )


def test_schema_initialization_command_does_not_start_export_or_wait(service):
    asyncio.run(worker.run(initialize=True))
    service.client.initialize.assert_awaited_once_with()
    service.make_exporter.assert_not_called()
    service.backlog.assert_not_awaited()
    service.sleep.assert_not_awaited()
    assert service.client.closed


@pytest.mark.parametrize("busy", [False, True])
def test_once_initializes_and_delivers_without_waiting(service, busy):
    service.exporter.once.return_value = busy
    asyncio.run(worker.run(once=True))
    service.client.initialize.assert_awaited_once_with()
    service.exporter.once.assert_awaited_once_with()
    service.backlog.assert_awaited_once_with(service.settings)
    service.sleep.assert_not_awaited()
    assert service.cycles == ["analytics.export"]
    assert service.client.closed


@pytest.mark.parametrize("failure", ["initialize", "delivery"])
def test_once_propagates_failure_and_closes_connections(service, failure):
    operation = service.client.initialize if failure == "initialize" else service.exporter.once
    operation.side_effect = RuntimeError("Analytics unavailable")
    with pytest.raises(RuntimeError, match="Analytics unavailable"):
        asyncio.run(worker.run(once=True))
    service.sleep.assert_not_awaited()
    assert service.client.closed


def test_service_retries_initialization_and_keeps_outage_visible(service):
    service.client.initialize.side_effect = [RuntimeError("ClickHouse restarting"), None]
    service.exporter.once.return_value = True
    service.sleep.side_effect = [None, StopService()]
    with pytest.raises(StopService):
        asyncio.run(worker.run())
    assert service.client.initialize.await_count == 2
    service.exporter.once.assert_awaited_once_with()
    service.backlog.assert_awaited_once_with(service.settings)
    assert [call.args[0] for call in service.sleep.await_args_list] == [2, 0.5]
    assert service.client.closed


def test_service_uses_independent_batch_delays_and_resets_failure_backoff(service):
    service.exporter.once.side_effect = [True, False, RuntimeError("Sink busy"), False]
    service.sleep.side_effect = [None, None, None, StopService()]
    with pytest.raises(StopService):
        asyncio.run(worker.run())
    assert [call.args[0] for call in service.sleep.await_args_list] == [0.5, 0.25, 2, 0.25]
    service.client.initialize.assert_awaited_once_with()
    assert service.client.closed


def test_backlog_sampling_is_throttled_and_failure_does_not_block_delivery(service, monkeypatch):
    moments = iter([0.0, 10.0, 31.0])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(moments))
    service.backlog.side_effect = [RuntimeError("Metrics database busy"), None]
    service.sleep.side_effect = [None, None, StopService()]
    with pytest.raises(StopService):
        asyncio.run(worker.run())
    assert service.backlog.await_count == 2
    assert service.exporter.once.await_count == 3


@pytest.mark.parametrize("age", [None, -10, 42])
def test_backlog_metric_uses_indexed_age_without_counting_pending_history(monkeypatch, age):
    now = datetime(2026, 10, 26, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, _tz=None):
            return now

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    result = NS(
        fetchone=AsyncMock(
            return_value={
                "oldest": now - timedelta(seconds=age) if age is not None else None,
            }
        )
    )
    connection = Connection()
    connection.execute = AsyncMock(return_value=result)
    connect = AsyncMock(return_value=connection)
    gauge = Mock()
    monkeypatch.setattr(worker, "current", lambda: NS(instruments={"read_model_oldest": gauge}))
    monkeypatch.setattr(worker, "datetime", Clock)
    monkeypatch.setattr(worker.psycopg, "AsyncConnection", NS(connect=connect))
    settings = NS(
        database_url="postgresql+psycopg://unused/devfeed_test",
        database_connect_timeout_seconds=3,
    )
    asyncio.run(worker.record_backlog(settings))
    queries = [call.args[0] for call in connection.execute.await_args_list]
    assert queries == [
        "SET statement_timeout = '2s'",
        "SELECT min(created_at) AS oldest FROM analytics_capture_events",
    ]
    gauge.set.assert_called_once_with(max(0, age or 0), {"kind": "analytics"})


def test_disabled_telemetry_does_not_access_postgres(monkeypatch):
    monkeypatch.setattr(worker, "current", lambda: None)
    connect = AsyncMock(side_effect=AssertionError("Metrics are disabled"))
    monkeypatch.setattr(worker.psycopg, "AsyncConnection", NS(connect=connect))
    asyncio.run(worker.record_backlog(None))
    connect.assert_not_awaited()


@pytest.mark.parametrize("failed", [False, True])
def test_cli_configures_and_closes_telemetry_on_success_and_failure(monkeypatch, failed):
    telemetry = object()
    configure, finish = Mock(), Mock()
    settings = NS(log_level="DEBUG", log_format="json")

    async def run(*args):
        assert args == (True, False)
        if failed:
            raise RuntimeError("Export failed")

    monkeypatch.setattr("sys.argv", ["analytics-worker", "--once"])
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_logging", configure)
    monkeypatch.setattr(worker, "start_runtime", lambda _service: telemetry)
    monkeypatch.setattr(worker, "stop_runtime", finish)
    monkeypatch.setattr(worker, "run", run)
    if failed:
        with pytest.raises(RuntimeError, match="Export failed"):
            worker.main()
    else:
        worker.main()
    configure.assert_called_once_with("analytics-worker", "DEBUG", "json")
    finish.assert_called_once_with(telemetry)


def test_busy_service_uses_normal_poll_after_bootstrap_has_completed(service):
    service.exporter.bootstrapping = False
    service.exporter.once.return_value = True
    service.sleep.side_effect = StopService()
    with pytest.raises(StopService):
        asyncio.run(worker.run())
    service.sleep.assert_awaited_once_with(service.settings.analytics_poll_seconds)
    assert service.client.closed
