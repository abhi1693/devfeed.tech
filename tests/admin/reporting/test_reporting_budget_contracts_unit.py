"""Reporting budgets and saved panels stay isolated from interactive requests.

Real SQLAlchemy engines/event dispatch exercise listener lifecycle. The driver
boundary records requested execution; PostgreSQL timeout enforcement remains an
integration contract. No SQL text or transaction behavior is simulated.
"""

import asyncio
from functools import lru_cache
from types import SimpleNamespace
from typing import get_args
from unittest.mock import Mock

import pytest
from devfeed_admin_api import overview, reporting
from devfeed_admin_api import overview_projection as projections
from devfeed_admin_api.overview_charts import CHART_SECTIONS, ChartName
from devfeed_admin_api.overview_sections import (
    AudienceSection,
    CoverageSection,
    InferenceSection,
    OperationsSection,
    OverviewSectionReport,
    ProcessingSection,
    PublishingSection,
)
from devfeed_admin_api.overview_windows import current_window, parse_report_window
from devfeed_core.read_models import ReadModelSnapshot
from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

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


SECTION_MODELS = {
    "operations": OperationsSection,
    "publishing": PublishingSection,
    "audience": AudienceSection,
    "processing": ProcessingSection,
    "inference": InferenceSection,
    "coverage": CoverageSection,
}


@pytest.mark.parametrize("status", [None, "fresh", "refreshing", "rebuilding"])
@pytest.mark.parametrize("chart", get_args(ChartName))
def test_saved_chart_marks_incomplete_snapshots_and_never_calculates_inline(
    monkeypatch, status, chart
):
    section = CHART_SECTIONS[chart]
    window = (
        current_window() if section == "operations" else parse_report_window("now-6d/d", "now", NOW)
    )
    saved = OverviewSectionReport(
        generated_at=NOW, days=window.days, data=SECTION_MODELS[section]()
    )
    if status:
        saved.snapshot = ReadModelSnapshot(
            status=status, generated_at=NOW, generation="section-v1", lag_seconds=12.8
        )
    factory = object()
    read = Mock(return_value=saved)
    calculate = Mock(side_effect=AssertionError("Inline calculation is forbidden"))
    monkeypatch.setattr(overview, "read_report", read)
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    monkeypatch.setattr(projections, "calculate_report_async", calculate)
    response = Response()
    output = asyncio.run(
        overview.overview_chart(
            chart, response, identity(), factory, from_value="now-6d/d", to_value="now"
        )
    )
    assert output.root.chart == chart
    assert output.root.generated_at == NOW and output.root.days == window.days
    assert "section" not in output.root.data.model_dump()
    read.assert_called_once_with(factory, section, window, OverviewSectionReport)
    stale = status in {"refreshing", "rebuilding"}
    assert response.headers.get("x-overview-stale") == ("true" if stale else None)
    assert response.headers.get("x-overview-age-seconds") == ("12" if stale else None)
    calculate.assert_not_called()
