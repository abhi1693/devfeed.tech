"""Reporting budgets and saved panels stay isolated from interactive requests.

Real SQLAlchemy engines/event dispatch exercise listener lifecycle. The driver
boundary records requested execution; PostgreSQL timeout enforcement remains an
integration contract. No SQL text or transaction behavior is simulated.
"""

import asyncio
from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import overview, overview_panels, reporting
from devfeed_admin_api.overview_panel_data import GROUPS, OverviewPanel
from devfeed_core.read_models import ReadModelSnapshot
from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from tests.admin.reporting.test_report_worker_policy_unit import overview_document
from tests.support.admin_editorial import identity
from tests.support.source_readiness import NOW, configuration


@pytest.fixture
def report_engine(monkeypatch):
    engine = create_engine("sqlite://")
    settings = configuration(database_pool_enabled=True)
    create = Mock(return_value=engine)
    monkeypatch.setattr(reporting, "get_settings", lambda: settings)
    monkeypatch.setattr(reporting, "create_database_engine", create)
    # A fresh cache cannot consume or dispose another application's engine.
    monkeypatch.setattr(
        reporting, "reporting_engine", lru_cache(maxsize=1)(reporting.reporting_engine.__wrapped__)
    )
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(reporting, "time", SimpleNamespace(monotonic=lambda: clock.now))
    yield SimpleNamespace(engine=engine, settings=settings, create=create, clock=clock)
    reporting.close_reporting()
    engine.dispose()


@pytest.mark.parametrize("elapsed", [1, 29, 30, 31])
def test_calculation_budget_admits_live_work_but_refuses_new_statements_at_deadline(
    report_engine, elapsed
):
    value = report_engine
    with value.engine.connect() as connection:
        cursor, session = Mock(), SimpleNamespace(connection=lambda: connection)
        with reporting.report_budget(session, seconds=30):
            value.clock.now += elapsed
            if elapsed >= 30:
                with pytest.raises(DatabaseTimeout, match="exceeded its time budget"):
                    connection.dispatch.before_cursor_execute(
                        connection, cursor, "read report", (), None, False
                    )
                cursor.execute.assert_not_called()
            else:
                connection.dispatch.before_cursor_execute(
                    connection, cursor, "read report", (), None, False
                )
                cursor.execute.assert_called_once()
        # Even an exhausted budget must not leak into subsequent connection use.
        value.clock.now += 60
        cursor.reset_mock()
        connection.dispatch.before_cursor_execute(connection, cursor, "later read", (), None, False)
        cursor.execute.assert_not_called()


def test_calculation_exception_always_removes_the_budget_listener(report_engine):
    value = report_engine
    with value.engine.connect() as connection:
        with (
            pytest.raises(RuntimeError, match="calculation failure"),
            reporting.report_budget(SimpleNamespace(connection=lambda: connection)),
        ):
            raise RuntimeError("calculation failure")
        value.clock.now += 100
        cursor = Mock()
        connection.dispatch.before_cursor_execute(connection, cursor, "later read", (), None, False)
        cursor.execute.assert_not_called()


@pytest.mark.parametrize("deadline", [None, 101.0, 100.0, 99.0])
def test_engine_budget_applies_only_when_that_connection_has_a_deadline(report_engine, deadline):
    value = report_engine
    assert reporting.reporting_engine() is value.engine
    cursor = Mock()
    with value.engine.connect() as connection:
        if deadline is not None:
            connection.info["report_deadline"] = deadline
        if deadline is not None and deadline <= value.clock.now:
            with pytest.raises(DatabaseTimeout, match="30-second budget"):
                value.engine.dispatch.before_cursor_execute(
                    connection, cursor, "read report", (), None, False
                )
            cursor.execute.assert_not_called()
        else:
            value.engine.dispatch.before_cursor_execute(
                connection, cursor, "read report", (), None, False
            )
            assert cursor.execute.call_count == int(deadline is not None)


def test_reporting_factory_uses_unpooled_materialized_sessions_and_disposes_cached_engine(
    report_engine, monkeypatch
):
    value = report_engine
    factory = reporting.reporting_sessions()
    with factory() as first, factory() as second:
        assert first is not second and first.get_bind() is second.get_bind() is value.engine
        assert first.expire_on_commit is False and second.expire_on_commit is False
    assert reporting.reporting_engine() is value.engine
    value.create.assert_called_once()
    (supplied,) = value.create.call_args.args
    assert supplied.database_pool_enabled is False and value.settings.database_pool_enabled is True
    dispose = Mock(wraps=value.engine.dispose)
    monkeypatch.setattr(value.engine, "dispose", dispose)
    reporting.close_reporting()
    reporting.close_reporting()
    dispose.assert_called_once_with()
    assert reporting.reporting_engine.cache_info().currsize == 0


@pytest.mark.parametrize("status", [None, "fresh", "refreshing", "rebuilding"])
@pytest.mark.parametrize("panel", ["job-reliability", "publication-time"])
def test_saved_panel_preserves_group_and_marks_only_stale_snapshots(monkeypatch, status, panel):
    saved = OverviewPanel(generated_at=NOW, days=7)
    if status:
        saved.snapshot = ReadModelSnapshot(
            status=status, generated_at=NOW, generation="report-v1", lag_seconds=12.8
        )
    factory = object()
    read = Mock(return_value=saved)
    monkeypatch.setattr(overview_panels, "read_report", read)
    response = Response()
    output = asyncio.run(
        overview_panels.overview_panel(panel, response, identity(), factory, days=7)
    )
    assert output is saved and output.generated_at == NOW and output.days == 7
    read.assert_called_once_with(factory, GROUPS.get(panel, panel), 7, OverviewPanel)
    stale = status in {"refreshing", "rebuilding"}
    assert response.headers.get("x-overview-stale") == ("true" if stale else None)
    assert response.headers.get("x-overview-age-seconds") == ("12" if stale else None)


@pytest.mark.parametrize("status", ["fresh", "refreshing"])
def test_saved_overview_uses_report_without_starting_an_inline_calculation(monkeypatch, status):
    from devfeed_admin_api import overview_projection

    saved = overview_document()
    saved.snapshot = ReadModelSnapshot(
        status=status, generated_at=NOW, generation="overview-v1", lag_seconds=32.4
    )
    read = Mock(return_value=saved)
    calculate = Mock(side_effect=AssertionError("Inline calculation is forbidden"))
    monkeypatch.setattr(overview_projection, "read_report", read)
    monkeypatch.setattr(overview, "overview_metrics", calculate)
    factory, response = object(), Response()
    assert asyncio.run(overview.overview(response, identity(), factory, days=30)) is saved
    read.assert_called_once_with(factory, "overview", 30, overview.AdminOverview)
    assert response.headers.get("x-overview-stale") == ("true" if status == "refreshing" else None)
    calculate.assert_not_called()
