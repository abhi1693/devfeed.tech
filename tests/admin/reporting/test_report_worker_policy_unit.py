"""Saved-report workers preserve retained daily facts and publish bounded freshness.

Scalars and aggregate rows are already scoped repository results. Captured write
parameters describe requested documents and cooldowns, not SQL locking or commit proof.
"""

import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from devfeed_admin_api import overview_projection as reports
from devfeed_admin_api import reporting
from devfeed_admin_api.overview_sections import OperationsSection, OverviewSectionReport
from devfeed_core.models import ReadModelState, ReportRefresh, ReportSnapshot
from devfeed_core.read_models import ReadModelSnapshot

from tests.support.overview import window_fields, window_for
from tests.support.source_readiness import NOW


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


@pytest.fixture
def report_store(monkeypatch):
    policy = reports.pipeline_policy_version()
    current = {
        "row": None,
        "pipeline": ReadModelState(
            name="pipeline",
            payload={
                "serving_policy": policy,
                "target_policy": policy,
                "backfill_complete": True,
                "article_generation": 99,
            },
            updated_at=NOW,
        ),
    }
    reads, writes = [], []
    session = Mock()
    session.info = {}
    session.connection.return_value.info = {}
    session.get.side_effect = lambda model, key, **kwargs: (
        current["pipeline"] if model is ReadModelState and key == "pipeline" else current["row"]
    )
    session.scalar.return_value = True

    def execute(statement):
        if statement.is_select:
            return Rows(reads.pop(0))
        writes.append(statement)

    session.execute.side_effect = execute

    @contextmanager
    def factory():
        yield session

    factory.begin = factory

    monkeypatch.setattr(reporting, "report_budget", lambda *_: nullcontext())
    return SimpleNamespace(
        current=current, reads=reads, writes=writes, session=session, factory=factory
    )


def panel(*, snapshot=None):
    return OverviewSectionReport(
        generated_at=NOW, days=7, data=OperationsSection(source_failures=12), snapshot=snapshot
    )


def overview_document():
    return panel()


@pytest.mark.parametrize("ready", [False, True])
def test_refresh_claim_converts_only_a_supplied_due_row_to_an_expiring_owned_attempt(
    report_store, ready
):
    value = report_store
    refresh = ReportRefresh(
        group="publishing",
        **window_fields("publishing", 7, now=NOW),
        requested_at=NOW - timedelta(minutes=10),
        available_at=NOW,
        attempts=2,
        lease_token=None,
        lease_until=None,
        last_error="TimeoutError",
    )
    value.session.scalar.return_value = refresh if ready else None
    claim = reports.claim_refresh(value.factory, NOW)
    if not ready:
        assert claim is None
        return
    assert claim == ("publishing", window_for("publishing", 7, NOW), refresh.lease_token, 3)
    assert isinstance(refresh.lease_token, uuid.UUID)
    assert refresh.lease_until == NOW + timedelta(seconds=45)
    assert (
        refresh.last_error == "TimeoutError"
    )  # The old complete result remains until publication.


@pytest.mark.parametrize("dependency", [False, True])
def test_report_publish_serializes_complete_content_and_dependency_before_retiring_owned_demand(
    report_store, dependency
):
    value = report_store
    claim = ("publishing", window_for("publishing", 7, NOW), uuid.uuid4(), 1)
    owned = ReportRefresh(
        group="publishing",
        **window_fields("publishing", 7, now=NOW),
        requested_at=NOW,
        available_at=NOW,
        attempts=1,
        lease_token=claim[2],
        lease_until=NOW + timedelta(seconds=30),
    )
    value.session.scalar.return_value = owned
    snapshot = (
        ReadModelSnapshot(
            status="catching_up",
            generation=f"{reports.pipeline_policy_version()}:4",
            policy_version=reports.pipeline_policy_version(),
            article_generation=4,
            catalog_generation=2,
            catalog_lag_seconds=600,
            generated_at=NOW - timedelta(seconds=2),
            lag_seconds=3,
        )
        if dependency
        else None
    )
    result = panel(snapshot=snapshot)
    assert reports.publish_report(value.factory, claim, result, NOW)
    requested = value.writes[-1].compile().params
    assert requested["group"] == "publishing" and requested["days"] == 7
    assert requested["generated_at"] == NOW
    assert requested["policy_version"] == reports.report_policy_version()
    assert requested["payload"]["data"]["source_failures"] == 12
    assert "snapshot" not in requested["payload"]
    assert requested["payload"].get("_projection_snapshot") == (
        snapshot.model_dump(mode="json") if snapshot else None
    )
    assert value.session.get.call_args_list == (
        [call(ReadModelState, "pipeline", with_for_update=True)] if dependency else []
    ) + [
        call(
            ReportSnapshot,
            ("publishing", window_for("publishing", 7, NOW).key),
            with_for_update=True,
        )
    ]
    value.session.delete.assert_called_once_with(owned)


@pytest.mark.parametrize("barrier", ["missing", "serving", "target", "incomplete", None])
def test_report_publication_fences_database_policy_changes_without_requiring_current_generation(
    report_store, barrier
):
    value = report_store
    policy = reports.pipeline_policy_version()
    claim = ("operations", window_for("operations", 1, NOW), uuid.uuid4(), 7)
    owned = ReportRefresh(
        group=claim[0],
        **window_fields(claim[0], now=NOW, window=claim[1]),
        requested_at=NOW,
        available_at=NOW,
        attempts=7,
        lease_token=claim[2],
        lease_until=NOW + timedelta(seconds=30),
    )
    value.session.scalar.return_value = owned
    snapshot = ReadModelSnapshot(
        status="catching_up",
        generation=f"{policy}:4",
        policy_version=policy,
        article_generation=4,
        lag_seconds=2,
    )
    stored = ReportSnapshot(
        group=claim[0],
        **window_fields(claim[0], now=NOW, window=claim[1]),
        payload={
            "articles_pending_review": 99,
            "_projection_snapshot": {
                "policy_version": policy,
                "article_generation": 3,
            },
        },
        generated_at=NOW - timedelta(seconds=1),
        # Different report policies deliberately skip same-policy ordering checks;
        # the authoritative pipeline pointer must still fence the old publisher.
        policy_version="newer-report-policy" if barrier else reports.report_policy_version(),
    )
    value.current["row"] = stored
    if barrier == "missing":
        value.current["pipeline"] = None
    elif barrier == "serving":
        value.current["pipeline"].payload.update(
            serving_policy="replacement-policy", target_policy="replacement-policy"
        )
    elif barrier == "target":
        value.current["pipeline"].payload["target_policy"] = "replacement-policy"
    elif barrier == "incomplete":
        value.current["pipeline"].payload["backfill_complete"] = False

    accepted = reports.publish_report(value.factory, claim, panel(snapshot=snapshot), NOW)

    assert accepted is (barrier is None)
    assert value.session.get.call_args_list[0] == call(
        ReadModelState, "pipeline", with_for_update=True
    )
    if barrier:
        assert value.writes == [] and value.current["row"] is stored
        assert stored.payload["articles_pending_review"] == 99
        assert owned.available_at == NOW + timedelta(seconds=5)
        assert owned.attempts == 0 and owned.lease_token is owned.lease_until is None
        assert owned.last_error == "ProjectionNotReady"
        value.session.delete.assert_not_called()
    else:
        # The pipeline advanced to generation99 after this report observed4.
        # Requiring equality here would starve publication under ingestion.
        assert value.current["pipeline"].payload["article_generation"] == 99
        assert value.writes[-1].compile().params["payload"]["_projection_snapshot"] == (
            snapshot.model_dump(mode="json")
        )
        value.session.delete.assert_called_once_with(owned)


def test_lost_refresh_claim_cannot_request_a_snapshot_write_or_remove_another_publishers_demand(
    report_store,
):
    value = report_store
    value.session.scalar.return_value = (
        None  # Ownership repository found no current unexpired claim.
    )
    assert not reports.publish_report(
        value.factory,
        ("publishing", window_for("publishing", 7, NOW), uuid.uuid4(), 1),
        panel(),
        NOW,
    )
    assert value.writes == []
    value.session.delete.assert_not_called()


@pytest.mark.parametrize(
    "barrier", ["report-policy", "pipeline-policy", "older-generated", "older-observation", None]
)
def test_stale_calculation_preserves_the_newer_complete_document_and_releases_its_claim(
    report_store, barrier
):
    value = report_store
    policy = reports.pipeline_policy_version()
    token = uuid.uuid4()
    claim = ("operations", window_for("operations", 1, NOW), token, 7)
    owned = ReportRefresh(
        group="operations",
        **window_fields("operations", 1, now=NOW),
        requested_at=NOW,
        available_at=NOW,
        attempts=7,
        lease_token=token,
        lease_until=NOW + timedelta(seconds=30),
    )
    value.session.scalar.return_value = owned
    stored = ReportSnapshot(
        group="operations",
        **window_fields("operations", 1, now=NOW),
        generated_at=NOW,
        policy_version=reports.report_policy_version(),
        payload={
            "articles_pending_review": 99,
            "_projection_snapshot": {
                "policy_version": policy,
                "article_generation": 5,
            },
        },
    )
    value.current["row"] = stored
    result = panel(
        snapshot=ReadModelSnapshot(
            status="fresh",
            policy_version=policy,
            article_generation=5,
        )
    )
    version = reports.report_policy_version()
    if barrier == "report-policy":
        version = "retired-report-policy"
    elif barrier == "pipeline-policy":
        result.snapshot.policy_version = "retired-pipeline-policy"
    elif barrier == "older-generated":
        result.generated_at = NOW - timedelta(microseconds=1)
    elif barrier == "older-observation":
        result.snapshot.article_generation = 4

    accepted = reports.publish_report(value.factory, claim, result, NOW, policy_version=version)

    assert accepted is (barrier is None)
    if barrier is None:
        value.session.delete.assert_called_once_with(owned)
        assert value.writes[-1].compile().params["payload"]["data"]["source_failures"] == 12
    else:
        assert value.current["row"] is stored
        assert stored.payload["articles_pending_review"] == 99
        assert value.writes == []
        assert owned.available_at == NOW + timedelta(seconds=5)
        assert owned.attempts == 0
        assert owned.lease_token is owned.lease_until is None
        assert owned.last_error == "ProjectionNotReady"
        value.session.delete.assert_not_called()


def test_repeated_range_independent_demand_preserves_a_live_lease_and_failure_cooldown():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    from sqlalchemy import MetaData

    # PostgreSQL validates canonical date arithmetic in integration; this SQLite
    # table exercises the real conflict update and lease/cooldown preservation.
    table = ReportRefresh.__table__.to_metadata(MetaData())
    table.constraints = {
        constraint
        for constraint in table.constraints
        if constraint.name != "ck_report_refresh_window"
    }
    table.create(engine)
    token = uuid.uuid4()
    available_at = NOW + timedelta(hours=1)
    try:
        with Session(engine) as session:
            session.add(
                ReportRefresh(
                    group="operations",
                    **window_fields("operations", 1, now=NOW),
                    requested_at=NOW,
                    available_at=available_at,
                    lease_token=token,
                    lease_until=NOW + timedelta(minutes=2),
                    attempts=5,
                    last_error="AnalyticsUnavailable",
                )
            )
            session.commit()
            for days in (7, 30, 90):
                reports.request_refresh(
                    session,
                    "operations",
                    window_for("operations", days, NOW + timedelta(minutes=1)),
                    NOW + timedelta(minutes=1),
                )
            reports.request_refresh(
                session, "operations", window_for("operations", 30, NOW), NOW + timedelta(hours=2)
            )
            reports.request_refresh(session, "publishing", window_for("publishing", 7, NOW), NOW)
            session.commit()
        with Session(engine) as session:
            queued = list(session.scalars(select(ReportRefresh).order_by(ReportRefresh.group)))
            assert [(row.group, row.days) for row in queued] == [
                ("operations", 1),
                ("publishing", 7),
            ]
            independent = queued[0]
            assert independent.last_requested_at == (NOW + timedelta(hours=2)).replace(tzinfo=None)
            assert independent.lease_token == token
            assert independent.attempts == 5
            assert independent.last_error == "AnalyticsUnavailable"
            assert independent.available_at == available_at.replace(tzinfo=None)
            assert independent.requested_at == NOW.replace(tzinfo=None)
            assert independent.lease_until == (NOW + timedelta(minutes=2)).replace(tzinfo=None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("attempt,delay", [(1, 15), (3, 60), (7, 900), (30, 900)])
def test_report_failure_requests_bounded_persistent_cooldown_with_safe_diagnostic_type(
    report_store, attempt, delay
):
    value = report_store
    token = uuid.uuid4()
    reports.fail_refresh(
        value.factory,
        ("publishing", window_for("publishing", 7, NOW), token, attempt),
        NOW,
        ValueError("postgres://private:credential@internal-host"),
    )
    requested = value.writes[-1].compile().params
    assert requested["available_at"] == NOW + timedelta(seconds=delay)
    assert requested["lease_token"] is requested["lease_until"] is None
    assert requested["last_error"] == "ValueError"
    assert "credential" not in str(requested)
    value.session.delete.assert_not_called()


@pytest.mark.parametrize("group", ["publishing", "audience", "processing", "inference", "coverage"])
def test_every_native_section_dispatches_without_opening_a_postgres_calculation(group):
    import asyncio
    from unittest.mock import AsyncMock

    factory = Mock(side_effect=AssertionError("Historical PostgreSQL calculation is forbidden"))
    client = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "ready": True,
                "updated_at": NOW.isoformat(),
                "capture_started_at": NOW.isoformat(),
                "lag_seconds": 0,
            }
        ),
        query=AsyncMock(return_value=[]),
    )
    result = asyncio.run(
        reports.calculate_report_async(factory, group, window_for(group, 7, NOW), NOW, client)
    )
    assert result.data.section == group and result.generated_at == NOW
    assert result.snapshot.status == "fresh" and result.snapshot.lag_seconds == 0
    assert result.snapshot.generation == f"analytics:clickhouse:{NOW.isoformat()}"
    assert client.query.await_count > 0
    factory.assert_not_called()


@pytest.mark.parametrize(
    "observed_age,source_lag,expected", [(120, 0, 120), (0, 90, 90), (0, 0, 0)]
)
def test_native_dispatch_preserves_observed_source_lag_instead_of_relabeling_old_data(
    observed_age, source_lag, expected
):
    import asyncio
    from unittest.mock import AsyncMock

    client = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "ready": True,
                "updated_at": (NOW - timedelta(seconds=observed_age)).isoformat(),
                "capture_started_at": NOW.isoformat(),
                "lag_seconds": source_lag,
            }
        ),
        query=AsyncMock(return_value=[]),
    )
    result = asyncio.run(
        reports.calculate_report_async(
            object(), "publishing", window_for("publishing", 7, NOW), NOW, client
        )
    )
    assert result.snapshot.lag_seconds == expected
    assert result.snapshot.status == ("stale" if expected > 60 else "fresh")
    assert result.snapshot.generated_at == NOW - timedelta(seconds=observed_age)


def test_native_dispatch_defers_initial_capture_without_querying_or_publishing_empty_charts():
    import asyncio
    from unittest.mock import AsyncMock

    client = SimpleNamespace(status=AsyncMock(return_value={"ready": False}), query=AsyncMock())
    with pytest.raises(reports.ProjectionNotReady, match="backfilling"):
        asyncio.run(
            reports.calculate_report_async(
                object(), "audience", window_for("audience", 30, NOW), NOW, client
            )
        )
    client.query.assert_not_awaited()


def test_unknown_native_sections_fail_before_contacting_either_store():
    import asyncio
    from unittest.mock import AsyncMock

    client = SimpleNamespace(status=AsyncMock(), query=AsyncMock())
    with pytest.raises(ValueError, match="Unknown analytical section"):
        asyncio.run(
            reports.calculate_report_async(
                object(), "retired-section", window_for("retired-section", 7, NOW), NOW, client
            )
        )
    client.status.assert_not_awaited()
    client.query.assert_not_awaited()


def test_operational_async_dispatch_finishes_its_short_transaction_before_returning(monkeypatch):
    import asyncio
    from contextlib import contextmanager
    from unittest.mock import AsyncMock

    closed = []
    session = Mock()
    session.connection.return_value.info = {}

    @contextmanager
    def factory():
        try:
            yield session
        finally:
            closed.append(True)

    expected = panel()
    monkeypatch.setattr(reporting, "report_budget", lambda *_: nullcontext())
    monkeypatch.setattr(reports, "operations_report", lambda *_: expected)
    client = SimpleNamespace(status=AsyncMock())
    assert (
        asyncio.run(
            reports.calculate_report_async(
                factory, "operations", window_for("operations", 1, NOW), NOW, client
            )
        )
        is expected
    )
    assert closed == [True]
    client.status.assert_not_awaited()


def test_native_readiness_timeout_cancels_work_without_trying_a_postgres_fallback(monkeypatch):
    import asyncio

    async def run():
        completed = asyncio.Event()

        async def waiting():
            try:
                await asyncio.Event().wait()
            finally:
                completed.set()

        original = asyncio.timeout
        monkeypatch.setattr(asyncio, "timeout", lambda _seconds: original(0.001))
        with pytest.raises(TimeoutError):
            await reports.calculate_report_async(
                object(),
                "audience",
                window_for("audience", 7, NOW),
                NOW,
                SimpleNamespace(status=waiting),
            )
        assert completed.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("limit", [0, 101])
def test_async_report_worker_rejects_unbounded_work_before_creating_clients(limit):
    import asyncio

    with pytest.raises(ValueError, match="between 1 and 100"):
        asyncio.run(reports.refresh_overview_projections_async(object(), limit=limit))


@pytest.mark.parametrize("claim_failure", [False, True])
def test_idle_worker_owns_and_closes_only_its_native_client(monkeypatch, claim_failure):
    import asyncio

    from devfeed_core import analytics

    lifecycle = []
    supplied = SimpleNamespace(status=Mock(), query=Mock())

    class OwnedClient:
        async def __aenter__(self):
            lifecycle.append("open")
            return supplied

        async def __aexit__(self, kind, *_args):
            lifecycle.append("failed-close" if kind else "close")

    factory = object()
    monkeypatch.setattr(analytics, "AnalyticsClient", OwnedClient)
    monkeypatch.setattr(reporting, "reporting_sessions", lambda: factory)
    monkeypatch.setattr(reports, "utcnow", lambda: NOW)
    claim = (
        Mock(side_effect=RuntimeError("claim unavailable"))
        if claim_failure
        else Mock(return_value=None)
    )
    monkeypatch.setattr(reports, "claim_refresh", claim)
    if claim_failure:
        with pytest.raises(RuntimeError, match="claim unavailable"):
            asyncio.run(reports.refresh_overview_projections_async())
    else:
        assert asyncio.run(reports.refresh_overview_projections_async()) == 0
    claim.assert_called_once_with(factory, NOW)
    assert lifecycle == ["open", "failed-close" if claim_failure else "close"]
    supplied.status.assert_not_called()
    supplied.query.assert_not_called()


@pytest.mark.parametrize("accepted", [False, True])
def test_worker_respects_its_claim_limit_and_does_not_treat_a_fenced_publish_as_failure(
    monkeypatch, accepted
):
    import asyncio
    from unittest.mock import AsyncMock

    factory, client = object(), object()
    claim = ("operations", window_for("operations", 1, NOW), uuid.uuid4(), 2)
    claimed = Mock(return_value=claim)
    calculated = AsyncMock(return_value=panel())
    published = Mock(return_value=accepted)
    failed = Mock()
    monkeypatch.setattr(reports, "utcnow", lambda: NOW)
    monkeypatch.setattr(reports, "claim_refresh", claimed)
    monkeypatch.setattr(reports, "calculate_report_async", calculated)
    monkeypatch.setattr(reports, "publish_report", published)
    monkeypatch.setattr(reports, "fail_refresh", failed)
    result = asyncio.run(
        reports.refresh_overview_projections_async(factory, limit=1, client=client)
    )
    assert result == int(accepted)
    claimed.assert_called_once_with(factory, NOW)
    calculated.assert_awaited_once_with(
        factory, "operations", window_for("operations", 1, NOW), NOW, client
    )
    published.assert_called_once_with(
        factory, claim, calculated.return_value, NOW, policy_version=reports.report_policy_version()
    )
    failed.assert_not_called()


def test_worker_defers_a_failed_dependency_and_continues_with_an_independent_section(
    monkeypatch, caplog
):
    import asyncio
    from unittest.mock import AsyncMock

    factory, client = object(), object()
    first, second = (
        ("publishing", window_for("publishing", 7, NOW), uuid.uuid4(), 9),
        ("operations", window_for("operations", 1, NOW), uuid.uuid4(), 1),
    )
    error = reports.ProjectionNotReady("private dependency diagnostic")
    claim = Mock(side_effect=[first, second, None])
    calculate = AsyncMock(side_effect=[error, panel()])
    publish, fail = Mock(return_value=True), Mock()
    monkeypatch.setattr(reports, "utcnow", lambda: NOW)
    monkeypatch.setattr(reports, "claim_refresh", claim)
    monkeypatch.setattr(reports, "calculate_report_async", calculate)
    monkeypatch.setattr(reports, "publish_report", publish)
    monkeypatch.setattr(reports, "fail_refresh", fail)
    assert asyncio.run(reports.refresh_overview_projections_async(factory, client=client)) == 1
    fail.assert_called_once_with(factory, first, NOW, error)
    assert publish.call_args.args[1] == second
    assert claim.call_count == 3
    assert "overview_projection_failed" in caplog.text
    assert "private dependency diagnostic" not in caplog.text


def test_operator_refresh_command_runs_the_required_async_publisher(monkeypatch):
    factory, client = object(), object()
    claimed = Mock(return_value=None)
    monkeypatch.setattr(reports, "claim_refresh", claimed)
    monkeypatch.setattr(reports, "utcnow", lambda: NOW)
    assert reports.refresh_overview_projections(factory, limit=1, now=NOW, client=client) == 0
    claimed.assert_called_once_with(factory, NOW)
