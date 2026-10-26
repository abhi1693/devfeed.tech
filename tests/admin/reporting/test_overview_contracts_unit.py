"""Read-only panels preserve calendar windows, unknown telemetry and independent refreshes."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api import automation, pipeline_reads
from devfeed_admin_api import overview_projection as projections
from devfeed_admin_api.overview_operations import operations_report
from devfeed_core.read_models import ReadModelSnapshot
from fastapi import HTTPException

from tests.support.overview import window_for
from tests.support.source_readiness import NOW


def test_attention_keeps_each_review_queue_and_its_oldest_timestamp_separate(monkeypatch):
    rows = [
        ("articles", 12, NOW - timedelta(days=4)),
        ("sources", 3, NOW - timedelta(days=2)),
        ("topics", 0, None),
        ("relationships", 5, NOW - timedelta(days=1)),
    ]
    monkeypatch.setattr(projections, "projected_blockers", lambda _: [])
    result = operations_report(
        SimpleNamespace(
            execute=lambda _: SimpleNamespace(all=lambda: rows), scalar=lambda _: 2, info={}
        ),
        30,
        NOW,
    ).data
    assert [(item.kind, item.count, item.oldest_at) for item in result.reviews] == rows
    assert result.source_failures == 2


def test_operational_blocker_panel_preserves_its_independent_result(monkeypatch):
    blocker = automation.AutomationBlocker(
        code="analysis_failed", label="Failed analysis", count=2, action="analyze", targets=[]
    )
    received = []
    monkeypatch.setattr(
        projections, "projected_blockers", lambda *args: received.append(args) or [blocker]
    )
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(all=lambda: []), scalar=lambda _: 0, info={}
    )
    result = operations_report(session, 7, NOW).data
    assert received == [(session,)]
    assert result.blockers == [blocker]


def test_unknown_panel_fails_instead_of_returning_an_empty_success():
    with pytest.raises(ValueError, match="Historical reports require ClickHouse"):
        projections.calculate_report(object(), "unknown", window_for("unknown", 7, NOW), NOW)


@pytest.mark.parametrize(
    "group,days,expected",
    [("publishing", 7, 7), ("audience", 90, 90), ("operations", 90, 1)],
)
def test_report_identity_shares_only_range_independent_panels(group, days, expected):
    assert projections.report_key(group, window_for(group, days, NOW)) == (
        group,
        window_for(group, expected, NOW).key,
    )


@pytest.mark.parametrize("group,days", [("unknown", 7), ("publishing", 0), ("publishing", 3661)])
def test_invalid_report_identity_is_rejected_before_recording_demand(group, days):
    with pytest.raises(ValueError, match="Unknown report group|between 1 and 3660"):
        projections.report_key(group, window_for(group, days, NOW))


@pytest.mark.parametrize("reason", ["rebuilding", "policy", "lag"])
def test_blocker_report_refuses_unready_or_incompatible_pipeline_evidence(monkeypatch, reason):
    def serving(_, **_kwargs):
        if reason == "rebuilding":
            raise HTTPException(503, "rebuilding")
        return (
            "old-policy" if reason == "policy" else projections.pipeline_policy_version(),
            ReadModelSnapshot(status="stale", lag_seconds=61 if reason == "lag" else 0),
        )

    monkeypatch.setattr(pipeline_reads, "serving_projection", serving)
    with pytest.raises(
        projections.ProjectionNotReady, match="catching up" if reason == "lag" else "rebuilding"
    ):
        projections.projected_blockers(object())


@pytest.mark.parametrize("compatible", [True, False])
@pytest.mark.parametrize("dependency_status", ["fresh", "refreshing"])
def test_blocker_report_uses_exact_projected_counts_and_revision_checked_targets(
    monkeypatch, compatible, dependency_status
):
    snapshot = ReadModelSnapshot(
        status=dependency_status,
        generated_at=NOW,
        lag_seconds=20 if dependency_status == "refreshing" else 0,
    )

    def serving(_session, *, kind):
        assert kind == "article"
        return projections.pipeline_policy_version(), snapshot

    monkeypatch.setattr(pipeline_reads, "serving_projection", serving)
    monkeypatch.setattr(
        automation,
        "automation_blockers",
        lambda *args, **kw: [
            automation.AutomationBlocker(
                code="analysis_failed",
                label="Failed analysis",
                count=0,
                action="analyze",
                targets=[],
            )
        ],
    )
    identifiers = [uuid.uuid4() for _ in range(5 if compatible else 1)]
    statements = []

    def execute(statement):
        statements.append(statement)
        return [
            SimpleNamespace(
                code="analysis_failed" if compatible else "retired-policy",
                total=34 if compatible else 1,
                id=identifier,
                title=f"Article requiring retry {index}",
                revision=7,
            )
            for index, identifier in enumerate(identifiers)
        ]

    session = SimpleNamespace(
        info={},
        execute=execute,
    )
    if not compatible:
        with pytest.raises(projections.ProjectionNotReady, match="incompatible"):
            projections.projected_blockers(session)
        return
    result = projections.projected_blockers(session)
    assert len(statements) == 1
    selected = statements[0].get_final_froms()[0].left
    assert set(selected.c.keys()) == {"id", "created_at", "code", "total", "position"}
    assert "AS MATERIALIZED" in str(statements[0])
    assert result[0].count == 34 and result[0].action == "analyze"
    assert result[0].targets == [
        automation.RecoveryTarget(
            id=identifier, title=f"Article requiring retry {index}", kind="article", revision=7
        )
        for index, identifier in enumerate(identifiers)
    ]
    assert session.info["report_projection_snapshot"] is snapshot


@pytest.mark.parametrize("limit", [0, 101])
def test_refresh_worker_rejects_unbounded_batches_before_touching_storage(limit):
    with pytest.raises(ValueError, match="between 1 and 100"):
        projections.refresh_overview_projections(object(), limit=limit)


@pytest.mark.parametrize("fails", [False, True])
def test_current_operations_calculation_closes_its_read_only_budget_and_session(monkeypatch, fails):
    from contextlib import contextmanager
    from unittest.mock import Mock

    from devfeed_admin_api import reporting
    from devfeed_admin_api.overview_sections import OperationsSection, OverviewSectionReport

    lifecycle = []
    session = Mock()
    session.connection.return_value.info = {}

    @contextmanager
    def factory():
        lifecycle.append("session-open")
        try:
            yield session
        finally:
            lifecycle.append("session-closed")

    @contextmanager
    def budget(received):
        assert received is session
        lifecycle.append("budget-open")
        try:
            yield
        finally:
            lifecycle.append("budget-closed")

    result = OverviewSectionReport(generated_at=NOW, days=1, data=OperationsSection())

    def calculate(received, days, stamp):
        assert (received, days, stamp) == (session, 1, NOW)
        assert lifecycle == ["session-open", "budget-open"]
        assert session.connection.return_value.info["report_deadline"] == 130
        assert [str(call.args[0]) for call in session.execute.call_args_list] == [
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
            "SET LOCAL statement_timeout = '10s'",
            "SET LOCAL idle_in_transaction_session_timeout = '10s'",
        ]
        lifecycle.append("calculate")
        if fails:
            raise projections.ProjectionNotReady("Pipeline rebuilding")
        return result

    monkeypatch.setattr(reporting, "report_budget", budget)
    monkeypatch.setattr(projections.clock, "monotonic", lambda: 100)
    monkeypatch.setattr(projections, "operations_report", calculate)
    if fails:
        with pytest.raises(projections.ProjectionNotReady, match="Pipeline rebuilding"):
            projections.calculate_report(
                factory, "operations", window_for("operations", 1, NOW), NOW
            )
    else:
        assert (
            projections.calculate_report(
                factory, "operations", window_for("operations", 1, NOW), NOW
            )
            is result
        )
    assert lifecycle == [
        "session-open",
        "budget-open",
        "calculate",
        "budget-closed",
        "session-closed",
    ]


@pytest.mark.parametrize(
    "query,valid",
    [
        (b"", True),
        (b"from=now-2y&to=now", True),
        (b"from=2024-11-01&to=2024-11-16", True),
        (b"range=P30D", False),
        (b"days=7", False),
        (b"from=2024-11-01&to=2024-11-16&start=2024-11-01", False),
    ],
)
def test_saved_http_reads_accept_only_the_from_to_protocol(query, valid):
    from devfeed_admin_api.overview import validate_report_query
    from starlette.requests import Request

    request = Request({"type": "http", "query_string": query})
    if valid:
        assert validate_report_query(request) is None
    else:
        with pytest.raises(HTTPException) as error:
            validate_report_query(request)
        assert error.value.status_code == 422


@pytest.mark.parametrize("lower,upper", [("bad", "now"), ("now", "now-1d"), ("now-3660d", "now")])
def test_invalid_calendar_windows_fail_before_saved_storage_is_opened(monkeypatch, lower, upper):
    import asyncio
    from unittest.mock import Mock

    from devfeed_admin_api import overview
    from starlette.responses import Response

    read = Mock(side_effect=AssertionError("Invalid dates reached saved storage"))
    monkeypatch.setattr(overview, "read_report", read)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            overview.overview_chart(
                "publication-rate", Response(), object(), object(), from_value=lower, to_value=upper
            )
        )
    assert error.value.status_code == 422
    read.assert_not_called()
