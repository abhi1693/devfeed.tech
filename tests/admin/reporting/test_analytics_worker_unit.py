"""Finite history stays retired while bounded current-state repair continues."""

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
    client = SimpleNamespace(check_schema=AsyncMock())
    status = SimpleNamespace(read=AsyncMock(), write=AsyncMock())
    monkeypatch.setattr(
        worker, "ClickHouseMigrationRunner", lambda _: SimpleNamespace(check=client.check_schema)
    )
    importer = SimpleNamespace(
        cutover=datetime(2026, 10, 1, tzinfo=UTC),
    )
    runner = SimpleNamespace(once=AsyncMock(), _importer=importer)
    monkeypatch.setattr(worker, "AnalyticsStatus", lambda _: status)
    monkeypatch.setattr(worker, "AnalyticsImportRunner", lambda *_, **__: runner)
    reconciler = SimpleNamespace(once=AsyncMock(return_value=False), close=AsyncMock())
    monkeypatch.setattr(worker, "CurrentStateReconciler", lambda *_, **__: reconciler)
    monkeypatch.setattr(worker, "pipeline_policy_version", lambda _: "current")
    monkeypatch.setattr(worker.AnalyticsImportWorker, "_write_heartbeat", Mock())
    return worker.AnalyticsImportWorker(client, settings), client, status, runner


def test_ready_restart_reconciles_current_state_without_replaying_history(service):
    exporter, client, status, runner = service
    status.read.return_value = {"ready": True, "policy": "current"}

    async def run():
        assert await exporter.once() is False
        assert await exporter.once() is False

    asyncio.run(run())
    runner.once.assert_not_called()
    status.write.assert_not_called()
    client.check_schema.assert_awaited_once()
    assert exporter.bootstrapping is False
    assert exporter.reconciler.once.await_count == 2


def test_current_repair_progress_and_failures_keep_history_checkpoint_intact(service):
    exporter, _, status, runner = service
    status.read.return_value = {"ready": True, "policy": "current"}
    exporter.reconciler.once.side_effect = [True, RuntimeError("Current repair unavailable")]

    async def run():
        assert await exporter.once() is True
        with pytest.raises(RuntimeError, match="Current repair unavailable"):
            await exporter.once()

    asyncio.run(run())
    runner.once.assert_not_called()
    status.write.assert_not_called()
    assert exporter.bootstrapping is False


def test_close_releases_the_current_state_coordinator(service):
    exporter, _, _, _ = service
    asyncio.run(exporter.close())
    exporter.reconciler.close.assert_awaited_once()


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


def test_complete_step_without_import_boundary_never_publishes_ready(service):
    exporter, _, status, runner = service
    status.read.side_effect = worker.AnalyticsUnavailable("not started")
    runner.once.return_value = worker.ImportStep.COMPLETE
    runner._importer = None
    with pytest.raises(RuntimeError, match="completed without a cutoff"):
        asyncio.run(exporter.once())
    assert status.write.await_count == 1
    assert status.write.await_args.kwargs["ready"] is False
    exporter._write_heartbeat.assert_not_called()


@pytest.mark.parametrize("cutover", [datetime(2026, 10, 1, tzinfo=UTC), "2026-10-01T00:00:00Z"])
def test_direct_status_preserves_cutoff_policy_and_current_readiness(cutover):
    from devfeed_core.analytics_status import AnalyticsStatus

    client = SimpleNamespace(execute=AsyncMock(), insert=AsyncMock(), query=AsyncMock())
    status = AnalyticsStatus(client)

    async def run():
        await status.write(ready=False, cutover=cutover, policy="new-policy")
        client.execute.assert_not_awaited()
        table, rows = client.insert.await_args.args
        assert table == "analytics_direct_status" and len(rows) == 1
        row = rows[0]
        assert row["ready"] == 0 and row["policy"] == "new-policy"
        assert row["capture_started_at"] == datetime(2026, 10, 1, tzinfo=UTC)
        assert row["reader_coverage_start"] == datetime(2026, 10, 1, tzinfo=UTC).date()
        assert row["version"] > 0 and row["updated_at"].utcoffset().total_seconds() == 0
        client.query.return_value = [row]
        read = await status.read()
        assert read["generated_at"] == row["updated_at"]
        assert read["policy"] == "new-policy" and read["ready"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("rows", [[], [{"ready": 1}, {"ready": 1}]])
def test_missing_or_ambiguous_direct_status_fails_closed(rows):
    from devfeed_core.analytics_status import AnalyticsStatus

    client = SimpleNamespace(query=AsyncMock(return_value=rows))
    with pytest.raises(worker.AnalyticsUnavailable, match="still running"):
        asyncio.run(AnalyticsStatus(client).read())
