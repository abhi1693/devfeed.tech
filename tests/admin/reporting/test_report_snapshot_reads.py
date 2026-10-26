"""Saved report availability and freshness decisions at the storage boundary."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api import overview_projection
from devfeed_admin_api.overview_sections import OperationsSection, OverviewSectionReport
from devfeed_core.models import ReadModelState, ReportRefresh, ReportSnapshot
from fastapi import HTTPException

from tests.support.overview import window_for

NOW = datetime(2026, 10, 21, tzinfo=UTC)
PIPELINE_POLICY = "pipeline-current"
DEFAULT_PIPELINE = object()


def pipeline_state(refresh=None):
    return ReadModelState(
        name="pipeline",
        payload={
            "serving_policy": PIPELINE_POLICY,
            "target_policy": PIPELINE_POLICY,
            "backfill_complete": True,
            "article_generation": 99,
            **({"catalog_refresh": refresh} if refresh else {}),
        },
        updated_at=NOW,
    )


class SavedReports:
    def __init__(self, stored=None, refresh=None, pipeline=DEFAULT_PIPELINE):
        self.stored = stored
        self.refresh = refresh
        self.pipeline = pipeline_state() if pipeline is DEFAULT_PIPELINE else pipeline
        self.events = []
        self.keys = []
        self.connection_info = {}

    @contextmanager
    def begin(self):
        self.events.append("begin")
        try:
            yield self
        except Exception:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    def connection(self):
        return SimpleNamespace(info=self.connection_info)

    def execute(self, statement):
        if getattr(statement, "is_select", False):
            from sqlalchemy.dialects import postgresql

            compiled = statement.compile(dialect=postgresql.dialect())
            query = str(compiled)
            assert "report_snapshots LEFT OUTER JOIN read_model_states" in query
            assert "read_model_states.name =" in query
            self.keys.extend(
                [(compiled.params["group_1"], compiled.params["window_key_1"]), "pipeline"]
            )
            pipeline = self.pipeline.payload if self.pipeline else None
            return SimpleNamespace(
                one_or_none=lambda: (self.stored, pipeline) if self.stored else None
            )
        assert str(statement).startswith(
            ("SET LOCAL statement_timeout", "SET LOCAL lock_timeout")
        ), "Saved report GET queried SQL outside its bounded snapshot metadata"

    def get(self, model, key):
        self.keys.append(key)
        if model is ReportSnapshot:
            return self.stored
        if model is ReportRefresh:
            return self.refresh
        if model is ReadModelState and key == "pipeline":
            return self.pipeline
        pytest.fail("Saved report GET queried a calculation dependency outside pipeline metadata")


@pytest.fixture
def saved_reports(monkeypatch):
    monkeypatch.setattr(overview_projection, "report_policy_version", lambda: "report-current")
    monkeypatch.setattr(overview_projection, "pipeline_policy_version", lambda: PIPELINE_POLICY)
    monkeypatch.setattr(
        overview_projection,
        "calculate_report",
        lambda *_: pytest.fail("Saved report GET started calculation"),
    )

    def request_refresh(store, group, window, now):
        store.events.append("demand")
        store.keys.append(overview_projection.report_key(group, window))
        if store.refresh is None:
            store.refresh = SimpleNamespace(last_error=None)

    monkeypatch.setattr(overview_projection, "request_refresh", request_refresh)
    return SavedReports


def stored_report(*, age=5, policy="report-current", dependency=None):
    generated = NOW - timedelta(seconds=age)
    payload = OverviewSectionReport(
        generated_at=generated, days=1, data=OperationsSection(source_failures=23)
    ).model_dump(mode="json")
    if dependency is not None:
        payload["_projection_snapshot"] = dependency
    return SimpleNamespace(
        payload=payload, generated_at=generated, policy_version=policy, last_requested_at=NOW
    )


@pytest.mark.parametrize(
    ("age", "policy", "dependency", "failed", "status", "demand"),
    [
        (5, "report-current", None, False, "fresh", False),
        (60, "report-current", None, False, "refreshing", True),
        (61, "report-current", None, True, "stale", True),
        (5, "report-old", None, False, "rebuilding", True),
        (5, "report-current", "rebuilding", False, "rebuilding", False),
        (5, "report-current", "refreshing", False, "refreshing", False),
        (5, "report-current", "stale", False, "stale", False),
        (5, "report-current", "fresh", False, "fresh", False),
    ],
)
def test_saved_report_preserves_payload_and_exposes_honest_freshness(
    saved_reports, age, policy, dependency, failed, status, demand
):
    metadata = (
        {
            "status": dependency,
            "lag_seconds": 7,
            "generation": f"{PIPELINE_POLICY}:17",
            "policy_version": PIPELINE_POLICY,
            "article_generation": 17,
        }
        if dependency
        else None
    )
    stored = stored_report(age=age, policy=policy, dependency=metadata)
    failure = SimpleNamespace(last_error="OperationalError") if failed else None
    store = saved_reports(stored, failure)
    result = overview_projection.read_report(
        store, "operations", window_for("operations", 30, NOW), OverviewSectionReport, now=NOW
    )
    assert result.days == 1
    assert result.data.source_failures == 23
    assert result.generated_at == stored.generated_at
    assert result.snapshot.status == status
    assert result.snapshot.generation == (metadata["generation"] if metadata else policy)
    assert result.snapshot.article_generation == (17 if metadata else None)
    assert result.snapshot.generated_at == stored.generated_at
    assert result.snapshot.lag_seconds == age + (7 if metadata else 0)
    assert store.events == (["begin", "demand", "commit"] if demand else ["begin", "commit"])
    assert set(store.keys) == {("operations", "current"), "pipeline"}
    if failed:
        assert store.refresh is failure


@pytest.mark.parametrize("group", ["processing", "publishing"])
@pytest.mark.parametrize(
    "age,status,demand", [(29, "fresh", False), (30, "refreshing", True), (31, "stale", True)]
)
def test_clickhouse_observation_age_requests_refresh_before_the_document_expires(
    saved_reports, group, age, status, demand
):
    generation = "analytics:clickhouse:2026-10-20T23:59:00+00:00"
    stored = stored_report(
        age=age,
        dependency={"status": "fresh", "lag_seconds": 30, "generation": generation},
    )
    store = saved_reports(stored)
    result = overview_projection.read_report(
        store, group, window_for(group, 30, NOW), OverviewSectionReport, now=NOW
    )

    assert age < overview_projection.FRESH_SECONDS
    assert result.snapshot.lag_seconds == age + 30
    assert result.snapshot.status == status
    assert result.snapshot.generation == generation
    assert result.generated_at == stored.generated_at
    assert result.data.source_failures == 23
    assert store.events == (["begin", "demand", "commit"] if demand else ["begin", "commit"])
    # Only saved result/demand keys are read. No historical source tables or
    # transactional pipeline dependencies participate in these analytical GETs.
    assert set(store.keys) == {(group, window_for(group, 30, NOW).key)}


def test_expired_clickhouse_observation_coalesces_demand_without_resetting_failure_cooldown(
    saved_reports,
):
    stored = stored_report(
        age=35,
        dependency={"status": "stale", "lag_seconds": 30, "generation": "analytics:clickhouse:old"},
    )
    cooldown = SimpleNamespace(
        last_error="AnalyticsUnavailable",
        available_at=NOW + timedelta(minutes=3),
        lease_until=NOW + timedelta(seconds=15),
        attempts=4,
    )
    store = saved_reports(stored, cooldown)
    for offset in (0, 1):
        result = overview_projection.read_report(
            store,
            "processing",
            window_for("processing", 30, NOW + timedelta(seconds=offset)),
            OverviewSectionReport,
            now=NOW + timedelta(seconds=offset),
        )
        assert result.snapshot.status == "stale"
        assert result.snapshot.generation == "analytics:clickhouse:old"
    assert store.refresh is cooldown
    assert cooldown.available_at == NOW + timedelta(minutes=3)
    assert cooldown.lease_until == NOW + timedelta(seconds=15)
    assert cooldown.attempts == 4 and cooldown.last_error == "AnalyticsUnavailable"
    assert store.events == ["begin", "demand", "commit"] * 2


@pytest.mark.parametrize("group", ["publishing", "operations"])
def test_cold_report_commits_refresh_demand_before_raising_retryable_response(saved_reports, group):
    store = saved_reports()
    with pytest.raises(HTTPException) as error:
        overview_projection.read_report(
            store, group, window_for(group, 17, NOW), OverviewSectionReport, now=NOW
        )
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "report_refreshing"
    assert error.value.headers["Retry-After"] == "5"
    assert store.events == ["begin", "demand", "commit"]
    key = overview_projection.report_key(group, window_for(group, 17, NOW))
    assert set(store.keys) == ({key, "pipeline"} if group != "publishing" else {key})


@pytest.mark.parametrize("group", ["operations", "operations", "publishing"])
@pytest.mark.parametrize("dependency", [None, "fresh", "refreshing"])
def test_pending_catalog_work_cannot_make_a_dependent_saved_report_look_fresh(
    saved_reports, group, dependency
):
    stored = stored_report(
        dependency={"status": dependency, "lag_seconds": 2} if dependency else None
    )
    pipeline = pipeline_state({"started_at": (NOW - timedelta(seconds=20)).isoformat()})
    store = saved_reports(stored, pipeline=pipeline)
    result = overview_projection.read_report(
        store, group, window_for(group, 7, NOW), OverviewSectionReport, now=NOW
    )
    dependent = group in {"operations"}
    assert result.snapshot.status == (
        "catching_up" if dependent else "refreshing" if dependency == "refreshing" else "fresh"
    )
    assert result.snapshot.generated_at == stored.generated_at
    assert result.snapshot.lag_seconds == (7 if dependency else 5)
    assert result.snapshot.catalog_lag_seconds == (20 if dependent else None)
    assert result.data.source_failures == 23
    assert ("demand" in store.events) is (dependent and dependency != "refreshing")
    assert ("pipeline" in store.keys) is dependent
    assert store.events[-1] == "commit"


@pytest.mark.parametrize("status", ["rebuilding", "stale"])
def test_active_catalog_refresh_preserves_existing_report_warning_and_cooldown(
    saved_reports, status
):
    stored = stored_report(dependency={"status": status, "lag_seconds": 45})
    cooldown = SimpleNamespace(last_error="OperationalError")
    pipeline = pipeline_state({"started_at": (NOW + timedelta(seconds=10)).isoformat()})
    store = saved_reports(stored, cooldown, pipeline)
    result = overview_projection.read_report(
        store, "operations", window_for("operations", 7, NOW), OverviewSectionReport, now=NOW
    )
    assert result.snapshot.status == status and result.snapshot.lag_seconds == 50
    assert store.refresh is cooldown
    assert store.events == ["begin", "commit"]


def test_legacy_dependency_without_a_generation_retains_the_saved_report_identity(saved_reports):
    stored = stored_report(dependency={"status": "fresh", "lag_seconds": 2})
    result = overview_projection.read_report(
        saved_reports(stored),
        "operations",
        window_for("operations", 7, NOW),
        OverviewSectionReport,
        now=NOW,
    )
    assert result.snapshot.generation == "report-current"
    assert result.snapshot.article_generation is None


def test_saved_catalog_lag_keeps_aging_until_a_new_document_is_published(saved_reports):
    stored = stored_report(
        age=10,
        dependency={
            "status": "catching_up",
            "lag_seconds": 2,
            "catalog_lag_seconds": 600,
            "generation": f"{PIPELINE_POLICY}:17",
            "policy_version": PIPELINE_POLICY,
            "article_generation": 17,
        },
    )
    store = saved_reports(stored)

    first = overview_projection.read_report(
        store, "operations", window_for("operations", 1, NOW), OverviewSectionReport, now=NOW
    )
    later = overview_projection.read_report(
        store,
        "operations",
        window_for("operations", 1, NOW + timedelta(seconds=15)),
        OverviewSectionReport,
        now=NOW + timedelta(seconds=15),
    )

    assert first.snapshot.catalog_lag_seconds == 610
    assert later.snapshot.catalog_lag_seconds == 625
    assert later.snapshot.lag_seconds == 27
    assert later.snapshot.status == "catching_up"
    assert later.snapshot.generation == first.snapshot.generation
    assert later.generated_at == first.generated_at == stored.generated_at
    assert later.data.source_failures == first.data.source_failures == 23


@pytest.mark.parametrize("group", ["operations", "operations", "publishing"])
def test_missing_current_pipeline_keeps_saved_content_but_requests_dependent_rebuild(
    saved_reports, group
):
    store = saved_reports(stored_report(), pipeline=None)
    result = overview_projection.read_report(
        store, group, window_for(group, 7, NOW), OverviewSectionReport, now=NOW
    )
    dependent = group != "publishing"
    assert result.data.source_failures == 23
    assert result.snapshot.status == ("rebuilding" if dependent else "fresh")
    assert ("demand" in store.events) is dependent


@pytest.mark.parametrize("age,status", [(1, "refreshing"), (2, "stale")])
def test_saved_catalog_report_ages_operational_lag_past_the_budget(saved_reports, age, status):
    stored = stored_report(age=age, dependency={"status": "catching_up", "lag_seconds": 59})
    pipeline = pipeline_state({"started_at": (NOW - timedelta(hours=1)).isoformat()})
    result = overview_projection.read_report(
        saved_reports(stored, pipeline=pipeline),
        "operations",
        window_for("operations", 7, NOW),
        OverviewSectionReport,
        now=NOW,
    )
    assert result.snapshot.status == status and result.snapshot.lag_seconds == age + 59
    assert result.snapshot.catalog_lag_seconds == 3600


def test_catalog_progress_does_not_clear_a_saved_stale_dependency_warning(saved_reports):
    stored = stored_report(dependency={"status": "stale", "lag_seconds": 2})
    pipeline = pipeline_state({"started_at": (NOW - timedelta(seconds=20)).isoformat()})
    result = overview_projection.read_report(
        saved_reports(stored, pipeline=pipeline),
        "operations",
        window_for("operations", 7, NOW),
        OverviewSectionReport,
        now=NOW,
    )
    assert result.snapshot.status == "stale"
    assert result.snapshot.lag_seconds == 7 and result.snapshot.catalog_lag_seconds == 20


def test_corrupt_document_commits_rebuild_demand_without_losing_the_original(saved_reports):
    stored = stored_report()
    stored.payload = {"invalid": True}
    store = saved_reports(stored)
    with pytest.raises(HTTPException) as error:
        overview_projection.read_report(
            store, "publishing", window_for("publishing", 7, NOW), OverviewSectionReport, now=NOW
        )
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "report_rebuilding"
    assert store.events == ["begin", "demand", "commit"]
    assert store.stored is stored


def test_invalid_dependency_keeps_complete_payload_visible_while_requesting_rebuild(saved_reports):
    store = saved_reports(stored_report(dependency={"status": "unknown"}))
    result = overview_projection.read_report(
        store, "publishing", window_for("publishing", 7, NOW), OverviewSectionReport, now=NOW
    )
    assert result.data.source_failures == 23
    assert result.snapshot.status == "rebuilding"
    assert store.events == ["begin", "demand", "commit"]


def test_future_saved_clock_does_not_create_negative_lag_or_refresh_storm(saved_reports):
    store = saved_reports(stored_report(age=-30))
    result = overview_projection.read_report(
        store, "publishing", window_for("publishing", 90, NOW), OverviewSectionReport, now=NOW
    )
    assert result.days == 90
    assert result.snapshot.status == "fresh"
    assert result.snapshot.lag_seconds == 0
    assert store.events == ["begin", "commit"]


@pytest.mark.parametrize("minutes,touched", [(59, False), (60, True), (61, True)])
def test_reading_a_saved_document_renews_its_retention_receipt_at_most_hourly(
    saved_reports, minutes, touched
):
    stored = stored_report()
    receipt = NOW - timedelta(minutes=minutes)
    stored.last_requested_at = receipt
    store = saved_reports(stored)
    result = overview_projection.read_report(
        store, "publishing", window_for("publishing", 7, NOW), OverviewSectionReport, now=NOW
    )
    assert result.data.source_failures == 23
    assert stored.last_requested_at == (NOW if touched else receipt)
    assert store.events == ["begin", "commit"]
