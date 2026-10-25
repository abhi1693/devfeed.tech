"""Saved-report workers preserve retained daily facts and publish bounded freshness.

Scalars and aggregate rows are already scoped repository results. Captured write
parameters describe requested documents and cooldowns, not SQL locking or commit proof.
"""

import copy
import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from devfeed_admin_api import overview, reporting
from devfeed_admin_api import overview_processing as processing
from devfeed_admin_api import overview_projection as reports
from devfeed_admin_api.overview_panel_data import OverviewPanel
from devfeed_core import reporting_facts
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.models import ReadModelState, ReportRefresh, ReportSnapshot
from devfeed_core.read_models import ReadModelSnapshot

from tests.support.source_readiness import NOW


def active_rows():
    return [
        {
            "kind": kind,
            "queued": 2 if kind == "analysis" else 0,
            "running": 1 if kind == "analysis" else 0,
            "oldest_queued_at": NOW - timedelta(days=100) if kind == "analysis" else None,
        }
        for kind in JOB_DEFINITIONS
    ]


def counts(completed=1, failed=0, duration_ms=100, duration_runs=1):
    return dict(
        completed=completed, failed=failed, duration_ms=duration_ms, duration_runs=duration_runs
    )


def rollup(*, version=processing.VERSION, cursor=0):
    return ReadModelState(
        name=processing.STATE_NAME,
        updated_at=NOW - timedelta(minutes=2),
        payload={
            "version": version,
            "cursor": cursor,
            "daily": {
                str((NOW - timedelta(days=index)).date()): {"analysis": counts(3)}
                for index in range(90)
            },
            "active": {
                row["kind"]: {
                    key: value.isoformat() if hasattr(value, "isoformat") else value
                    for key, value in row.items()
                    if key != "kind"
                }
                for row in active_rows()
            },
        },
    )


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

    def save(session, name, payload):
        current["row"] = ReadModelState(name=name, payload=payload, updated_at=NOW)

    monkeypatch.setattr(processing, "set_state", save)
    monkeypatch.setattr(reporting, "report_budget", lambda *_: nullcontext())
    monkeypatch.setattr(reporting_facts, "require_reporting_facts", lambda *_: None)
    monkeypatch.setattr(
        reporting_facts,
        "reporting_fact_observation",
        lambda *_: {"observed_at": NOW.isoformat(), "lag_seconds": 0, "generation": 8},
    )
    return SimpleNamespace(
        current=current, reads=reads, writes=writes, session=session, factory=factory
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_initial_daily_backfill_fills_one_bounded_history_and_replaces_incompatible_version(
    report_store, legacy
):
    value = report_store
    value.current["row"] = rollup(version="old-schema") if legacy else None
    first = NOW.date() - timedelta(days=89)
    value.reads.extend(
        [
            [
                {"day": first, "kind": "analysis", **counts(4, 1, 300, 2)},
                {"day": NOW.date(), "kind": "topic-analysis", **counts(2, 0, 500, 1)},
            ],
            active_rows(),
        ]
    )
    assert processing.refresh_processing_facts(value.factory, NOW)
    stored = value.current["row"]
    assert stored.updated_at == NOW and stored.payload["version"] == processing.VERSION
    assert len(stored.payload["daily"]) == 90 and min(stored.payload["daily"]) == str(first)
    assert stored.payload["daily"][str(first)]["analysis"] == counts(4, 1, 300, 2)
    assert stored.payload["daily"][str(NOW.date())] == {"topic-analysis": counts(2, 0, 500, 1)}
    assert stored.payload["cursor"] == 1 and not value.reads
    facts = {
        row["kind"]: row
        for row in processing.processing_facts(value.session, NOW.replace(hour=0), NOW)
    }
    assert facts["analysis"]["completed"] == 0
    assert facts["analysis"]["queued"] == 2 and facts["analysis"]["running"] == 1
    assert facts["analysis"]["oldest_queued_at"] == (NOW - timedelta(days=100)).isoformat()
    assert facts["topic-analysis"]["completed"] == 2


@pytest.mark.parametrize("cursor", [0, 89])
def test_daily_reconciliation_retains_closed_history_and_rotates_one_older_day(
    report_store, cursor
):
    value = report_store
    stored = rollup(cursor=cursor)
    old_day = NOW.date() - timedelta(days=95)
    stored.payload["daily"][str(old_day)] = {"analysis": counts(99)}
    value.current["row"] = stored
    first = NOW.date() - timedelta(days=89)
    rotated = first + timedelta(days=cursor % 88)
    retained = NOW.date() - timedelta(days=40)
    retained_counts = copy.deepcopy(stored.payload["daily"][str(retained)])
    value.reads.extend(
        [
            [{"day": NOW.date(), "kind": "analysis", **counts(7, 2, 900, 3)}],
            [{"day": rotated, "kind": "analysis", **counts(5)}],
            active_rows(),
        ]
    )
    assert processing.refresh_processing_facts(value.factory, NOW)
    payload = value.current["row"].payload
    assert len(payload["daily"]) == 90 and str(old_day) not in payload["daily"]
    assert payload["daily"][str(retained)] == retained_counts
    assert payload["daily"][str(NOW.date() - timedelta(days=1))] == {}
    assert payload["daily"][str(rotated)] == {"analysis": counts(5)}
    assert payload["daily"][str(NOW.date())] == {"analysis": counts(7, 2, 900, 3)}
    assert payload["cursor"] == cursor % 88 + 1 and not value.reads


@pytest.mark.parametrize("barrier", ["busy", "fresh"])
def test_daily_worker_declines_busy_or_recent_rollups_without_starting_aggregate_reads(
    report_store, barrier
):
    value = report_store
    value.session.scalar.return_value = barrier != "busy"
    stored = rollup()
    stored.updated_at = NOW - timedelta(seconds=59)
    value.current["row"] = stored
    before = copy.deepcopy(stored.payload)
    assert not processing.refresh_processing_facts(value.factory, NOW)
    assert stored.payload == before and value.current["row"] is stored
    value.session.flush.assert_not_called()


@pytest.mark.parametrize("missing", ["row", "version", "start", "end"])
def test_processing_report_uses_legacy_fallback_when_retained_coverage_cannot_answer(
    report_store, missing
):
    value = report_store
    stored = rollup(version="old-schema" if missing == "version" else processing.VERSION)
    start = NOW - timedelta(days=2)
    if missing in {"start", "end"}:
        stored.payload["daily"].pop(str((start if missing == "start" else NOW).date()))
    value.current["row"] = None if missing == "row" else stored
    assert processing.processing_facts(value.session, start, NOW) is None


def panel(*, snapshot=None):
    return OverviewPanel(generated_at=NOW, days=7, articles_pending_review=12, snapshot=snapshot)


def overview_document():
    return overview.AdminOverview(
        generated_at=NOW,
        days=7,
        articles=20,
        articles_pending_review=12,
        articles_published=8,
        sources=5,
        sources_pending_review=1,
        sources_active=4,
        sources_failing=0,
        topics=3,
        topics_active=3,
        topic_proposals_pending=0,
        relationship_proposals_pending=0,
        activity=[],
        top_topics=[],
        analysis=overview.OverviewAnalysis(queued=2, running=1, succeeded=8, failed=0),
    )


@pytest.mark.parametrize("ready", [False, True])
def test_refresh_claim_converts_only_a_supplied_due_row_to_an_expiring_owned_attempt(
    report_store, ready
):
    value = report_store
    refresh = ReportRefresh(
        group="daily",
        days=7,
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
    assert claim == ("daily", 7, refresh.lease_token, 3)
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
    claim = ("daily", 7, uuid.uuid4(), 1)
    owned = ReportRefresh(
        group="daily",
        days=7,
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
    observation = {
        "observed_at": (NOW - timedelta(seconds=4)).isoformat(),
        "lag_seconds": 3,
        "generation": 8,
    }
    result._reporting_fact_observation = observation
    assert reports.publish_report(value.factory, claim, result, NOW)
    requested = value.writes[-1].compile().params
    assert requested["group"] == "daily" and requested["days"] == 7
    assert requested["generated_at"] == NOW
    assert requested["policy_version"] == reports.report_policy_version()
    assert requested["payload"]["articles_pending_review"] == 12
    assert "snapshot" not in requested["payload"]
    assert requested["payload"]["_reporting_fact_observation"] == observation
    assert requested["payload"].get("_projection_snapshot") == (
        snapshot.model_dump(mode="json") if snapshot else None
    )
    assert value.session.get.call_args_list == (
        [call(ReadModelState, "pipeline", with_for_update=True)] if dependency else []
    ) + [call(ReportSnapshot, ("daily", 7), with_for_update=True)]
    value.session.delete.assert_called_once_with(owned)


@pytest.mark.parametrize("barrier", ["missing", "serving", "target", "incomplete", None])
def test_report_publication_fences_database_policy_changes_without_requiring_current_generation(
    report_store, barrier
):
    value = report_store
    policy = reports.pipeline_policy_version()
    claim = ("blockers", 1, uuid.uuid4(), 7)
    owned = ReportRefresh(
        group=claim[0],
        days=claim[1],
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
        days=claim[1],
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
    assert not reports.publish_report(value.factory, ("daily", 7, uuid.uuid4(), 1), panel(), NOW)
    assert value.writes == []
    value.session.delete.assert_not_called()


@pytest.mark.parametrize("attempt,delay", [(1, 15), (3, 60), (7, 900), (30, 900)])
def test_report_failure_requests_bounded_persistent_cooldown_with_safe_diagnostic_type(
    report_store, attempt, delay
):
    value = report_store
    token = uuid.uuid4()
    reports.fail_refresh(
        value.factory,
        ("daily", 7, token, attempt),
        NOW,
        ValueError("postgres://private:credential@internal-host"),
    )
    requested = value.writes[-1].compile().params
    assert requested["available_at"] == NOW + timedelta(seconds=delay)
    assert requested["lease_token"] is requested["lease_until"] is None
    assert requested["last_error"] == "ValueError"
    assert "credential" not in str(requested)
    value.session.delete.assert_not_called()


@pytest.mark.parametrize("group", ["processing", "overview", "daily"])
def test_worker_calculation_keeps_dependency_and_oldest_rollup_calculation_time(
    report_store, monkeypatch, group
):
    value = report_store
    stored = rollup()
    stored.updated_at = NOW - timedelta(seconds=5)
    value.current["row"] = stored
    dependency = ReadModelSnapshot(
        status="rebuilding",
        generation="pipeline-policy",
        generated_at=NOW - timedelta(seconds=4),
        lag_seconds=2,
    )
    value.session.info["report_projection_snapshot"] = dependency
    supplied = overview_document() if group == "overview" else panel()
    calculate = Mock(return_value=supplied)
    monkeypatch.setattr(reports, "panel_data", calculate)
    monkeypatch.setattr(overview, "overview_metrics", calculate)
    result = reports.calculate_report(value.factory, group, 7, NOW)
    assert result is supplied and result.snapshot == dependency
    assert result.generated_at == (NOW if group == "daily" else stored.updated_at)
    if group == "overview":
        calculate.assert_called_once_with(value.session, 7, projected=True)
    else:
        calculate.assert_called_once_with(value.session, group, 7, NOW)


@pytest.mark.parametrize("barrier", ["missing", "stale", "incomplete"])
def test_processing_calculation_cannot_publish_before_recent_complete_daily_coverage(
    report_store, monkeypatch, barrier
):
    value = report_store
    stored = rollup()
    stored.updated_at = NOW - timedelta(seconds=61 if barrier == "stale" else 5)
    if barrier == "incomplete":
        stored.payload["daily"].pop(str(NOW.date()))
    value.current["row"] = None if barrier == "missing" else stored
    calculate = Mock()
    monkeypatch.setattr(reports, "panel_data", calculate)
    with pytest.raises(reports.ProjectionNotReady, match="rollups are still rebuilding"):
        reports.calculate_report(value.factory, "processing", 7, NOW)
    calculate.assert_not_called()


@pytest.mark.parametrize("group", sorted(reports.FACT_REPORT_GROUPS))
def test_worker_declines_incomplete_reporting_facts_before_running_a_report(
    report_store, monkeypatch, group
):
    require = Mock(side_effect=reporting_facts.ReportingFactsUnavailable("Rebuilding"))
    monkeypatch.setattr(reporting_facts, "require_reporting_facts", require)
    calculate = Mock()
    monkeypatch.setattr(reports, "panel_data", calculate)
    monkeypatch.setattr(overview, "overview_metrics", calculate)
    with pytest.raises(reports.ProjectionNotReady, match="Reporting facts are still rebuilding"):
        reports.calculate_report(report_store.factory, group, 30, NOW)
    calculate.assert_not_called()


def test_worker_records_fact_watermark_and_lag_from_the_calculation_snapshot(
    report_store, monkeypatch
):
    observation = {
        "observed_at": (NOW - timedelta(seconds=15)).isoformat(),
        "lag_seconds": 12,
        "generation": 8,
    }
    monkeypatch.setattr(reporting_facts, "reporting_fact_observation", lambda *_: observation)
    result = panel()
    monkeypatch.setattr(reports, "panel_data", lambda *_: result)
    actual = reports.calculate_report(report_store.factory, "job-tokens", 30, NOW)
    assert actual._reporting_fact_observation == observation
    assert actual.snapshot.generation == "reporting-facts:8"
    assert actual.snapshot.lag_seconds == 12
    assert report_store.session.info["reporting_facts"] is True


def test_refresh_seeding_uses_recent_viewers_and_the_small_warm_set(monkeypatch, report_store):
    value = report_store
    version = reports.report_policy_version()

    def stored(model, key):
        return ReportSnapshot(
            group=key[0],
            days=key[1],
            generated_at=NOW - timedelta(seconds=300 if key == ("daily", 14) else 1),
            policy_version=version,
            payload=(overview_document() if key[0] == "overview" else panel()).model_dump(
                mode="json"
            ),
        )

    value.session.get.side_effect = stored
    value.reads.append([("daily", 14), ("sources", 90)])
    demand = Mock()
    monkeypatch.setattr(reports, "request_refresh", demand)
    reports.seed_refreshes(value.factory, NOW)
    assert [call.args[1:] for call in demand.call_args_list] == [
        ("daily", 14, NOW),
    ]
