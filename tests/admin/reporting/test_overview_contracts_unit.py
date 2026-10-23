"""Read-only panels preserve calendar windows, unknown telemetry and independent refreshes."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api import automation, overview_processing, pipeline_reads
from devfeed_admin_api import overview_panel_data as panels
from devfeed_admin_api import overview_projection as projections
from devfeed_admin_api.overview_insights import (
    OverviewAdoption,
    OverviewInterestCoverage,
    OverviewPersonalization,
    OverviewProcessing,
    OverviewSourcePerformance,
)
from devfeed_core.inference_metrics import InferenceCharts
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.models import ReadModelState
from devfeed_core.pipeline_metrics import PipelineThroughput
from devfeed_core.read_models import ReadModelSnapshot
from devfeed_core.topic_decision_metrics import TopicDecisionMetrics
from fastapi import HTTPException

from tests.support.source_readiness import NOW


def daily_values(**changes):
    return {"added": 4, "published": 2, "accounts": 1, "opens": 3, **changes}


def test_daily_panel_compares_equal_calendar_windows_without_turning_missing_opens_into_zero(
    monkeypatch,
):
    loaded = []
    daily = {
        (NOW - timedelta(days=3)).date(): daily_values(published=5, opens=9),
        (NOW - timedelta(days=2)).date(): daily_values(published=3, opens=None),
        (NOW - timedelta(days=1)).date(): daily_values(published=7, opens=11),
        NOW.date(): daily_values(published=1, opens=2),
    }

    def read(session, start, end):
        loaded.append((start, end))
        return daily

    monkeypatch.setattr(panels, "read_daily_metrics", read)
    result = panels.panel_data(object(), "daily", 2, NOW)

    assert loaded == [(NOW.replace(hour=0) - timedelta(days=3), NOW)]
    assert result.generated_at == NOW and result.days == 2
    assert result.insights.publications.model_dump() == {"current": 8, "previous": 8}
    assert result.insights.opens.model_dump() == {"current": 13, "previous": None}
    assert result.insights.accounts.model_dump() == {"current": 2, "previous": 2}
    assert [day.date for day in result.insights.reader_activity] == [
        str((NOW - timedelta(days=1)).date()),
        str(NOW.date()),
    ]
    assert result.automation is None


def test_publication_latency_uses_reported_article_percentiles_not_daily_percentiles(monkeypatch):
    daily = {
        (NOW - timedelta(days=index)).date(): daily_values(median_publication_seconds=1000)
        for index in range(14)
    }
    monkeypatch.setattr(panels, "read_daily_metrics", lambda *_: daily)
    session = SimpleNamespace(execute=lambda _: SimpleNamespace(one=lambda: (30.0, 90.0, 240.0)))
    result = panels.panel_data(session, "publication-time", 7, NOW)
    assert result.insights.publication_seconds.model_dump() == {"current": 30, "previous": 90}
    assert result.insights.publication_p90_seconds == 240
    assert result.insights.publications.current == 14


@pytest.mark.parametrize(
    "group", ["adoption", "personalization", "topic-coverage", "sources", "processing"]
)
def test_insight_panel_loads_only_its_dataset_and_retains_nonzero_results(monkeypatch, group):
    identifier = uuid.uuid4()
    results = {
        "adoption": OverviewAdoption(accounts=10, liking=3),
        "personalization": OverviewPersonalization(ready=4, refreshing=2),
        "topic-coverage": [
            OverviewInterestCoverage(
                id=identifier,
                name="Databases",
                logo_url=None,
                followers=3,
                inferred_users=5,
                publications=8,
                last_published_at=NOW,
            )
        ],
        "sources": (
            [
                OverviewSourcePerformance(
                    id=identifier,
                    name="Engineering",
                    logo_url=None,
                    discovered=12,
                    published=7,
                    followers=3,
                    fetch_failures=2,
                    consecutive_failures=1,
                    last_success_at=NOW,
                )
            ],
            1,
            15,
        ),
        "processing": [
            OverviewProcessing(
                kind="analysis",
                queued=3,
                running=1,
                completed=10,
                failed=2,
                oldest_queued_at=NOW - timedelta(days=10),
            )
        ],
    }
    loaders = {
        "adoption": "reader_adoption",
        "personalization": "personalization",
        "topic-coverage": "interest_coverage",
        "sources": "sources_performance",
        "processing": "processing",
    }
    received = []
    for key, name in loaders.items():

        def load(*args, key=key):
            assert key == group, "an unrelated panel dataset was loaded"
            received.append(args)
            return results[key]

        monkeypatch.setattr(panels, name, load)
    session = object()

    result = panels.panel_data(session, group, 7, NOW)

    expected_args = (
        (session,)
        if group == "adoption"
        else (session, NOW)
        if group == "personalization"
        else (session, NOW.replace(hour=0) - timedelta(days=6), NOW)
    )
    assert received == [expected_args]
    field = {"topic-coverage": "coverage", "sources": "source_performance"}.get(group, group)
    assert getattr(result.insights, field) == (
        results[group][0] if group == "sources" else results[group]
    )
    if group == "sources":
        assert (
            result.insights.failing_sources == 1 and result.insights.source_publications_total == 15
        )
    assert result.automation is None


@pytest.mark.parametrize("days,retained_days", [(7, 7), (90, 30)])
def test_popular_articles_explain_retention_limit_and_preserve_read_totals(
    monkeypatch, days, retained_days
):
    identifier, received = uuid.uuid4(), []
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(
            mappings=lambda: [
                {"id": identifier, "title": "A useful article", "opens": 23, "likes": 4}
            ]
        )
    )

    def daily(session, start, end):
        received.append((start, end))
        return {
            (NOW - timedelta(days=index)).date(): daily_values(opens=0 if index else 23)
            for index in reversed(range(retained_days))
        }

    monkeypatch.setattr(panels, "read_daily_metrics", daily)

    result = panels.panel_data(session, "popular-articles", days, NOW)

    assert received == [(NOW.replace(hour=0) - timedelta(days=retained_days - 1), NOW)]
    assert result.insights.top_articles_days == retained_days
    assert result.insights.top_articles[0].model_dump() == {
        "id": identifier,
        "title": "A useful article",
        "opens": 23,
        "likes": 4,
    }
    assert len(result.insights.reader_activity) == retained_days
    assert result.insights.reader_activity[-1].opens == 23


def test_attention_keeps_each_review_queue_and_its_oldest_timestamp_separate():
    rows = [
        ("articles", 12, NOW - timedelta(days=4)),
        ("sources", 3, NOW - timedelta(days=2)),
        ("topics", 0, None),
        ("relationships", 5, NOW - timedelta(days=1)),
    ]
    result = panels.panel_data(
        SimpleNamespace(execute=lambda _: SimpleNamespace(all=lambda: rows), scalar=lambda _: 2),
        "attention",
        30,
        NOW,
    )
    assert (
        result.articles_pending_review,
        result.sources_pending_review,
        result.topic_proposals_pending,
        result.relationship_proposals_pending,
    ) == (12, 3, 0, 5)
    assert result.sources_failing == 2
    assert result.insights.oldest_review_at == {key: oldest for key, _, oldest in rows}


def test_completed_job_tokens_include_verification_and_keep_unreported_runs_distinct(monkeypatch):
    activity = [
        automation.AnalysisTokenDay(
            date="2026-10-21",
            article_analysis=100,
            topic_analysis=30,
            research_verification=20,
            reported_runs=2,
            unreported_runs=1,
        ),
        automation.AnalysisTokenDay(
            date="2026-10-22",
            article_analysis=40,
            topic_analysis=10,
            research_verification=5,
            reported_runs=1,
        ),
    ]
    monkeypatch.setattr(panels, "analysis_token_activity", lambda *_: (activity, 3200))
    result = panels.panel_data(object(), "job-tokens", 7, NOW).automation
    assert result.analysis_tokens == 205 and result.analysis_duration_ms == 3200
    assert result.usage_reported_runs == 3
    assert result.token_activity[0].unreported_runs == 1


@pytest.mark.parametrize(
    "group", ["blockers", "publication-automation", "throughput", "decisions", "inference"]
)
def test_automation_panels_preserve_their_independent_result(monkeypatch, group):
    blocker = automation.AutomationBlocker(
        code="analysis_failed", label="Failed analysis", count=2, action="analyze", targets=[]
    )
    results = {
        "blockers": [blocker],
        "publication-automation": {
            "published_in_window": 8,
            "published_without_intervention": 6,
            "automatic_publication_percent": 75.0,
        },
        "throughput": PipelineThroughput(),
        "decisions": TopicDecisionMetrics(),
        "inference": InferenceCharts(),
    }
    owner, name = {
        "blockers": (projections, "projected_blockers"),
        "publication-automation": (panels, "publication_automation"),
        "throughput": (panels, "pipeline_throughput"),
        "decisions": (panels, "decision_metrics"),
        "inference": (panels, "inference_charts"),
    }[group]
    received = []
    monkeypatch.setattr(owner, name, lambda *args: received.append(args) or results[group])
    session = object()
    result = panels.panel_data(session, group, 7, NOW).automation
    expected_args = (
        (session,)
        if group == "blockers"
        else (session, NOW)
        if group == "throughput"
        else (session, NOW.replace(hour=0) - timedelta(days=6), NOW)
    )
    assert received == [expected_args]
    if group == "publication-automation":
        assert result.published_in_window == 8 and result.published_without_intervention == 6
        assert result.automatic_publication_percent == 75
    else:
        field = {"decisions": "topic_decisions"}.get(group, group)
        assert getattr(result, field) == results[group]


def test_unknown_panel_fails_instead_of_returning_an_empty_success():
    with pytest.raises(ValueError, match="Unknown Overview panel"):
        panels.panel_data(object(), "unknown", 7, NOW)


@pytest.mark.parametrize("missing", ["state", "version", "start", "end"])
def test_processing_rollup_declines_incomplete_history_so_caller_can_fall_back(missing):
    daily = {str((NOW - timedelta(days=1)).date()): {}, str(NOW.date()): {}}
    payload = {"version": overview_processing.VERSION, "daily": daily, "active": {}}
    if missing == "version":
        payload["version"] = "legacy"
    if missing in {"start", "end"}:
        daily.pop(str((NOW - timedelta(days=1)).date()) if missing == "start" else str(NOW.date()))
    stored = (
        None
        if missing == "state"
        else ReadModelState(name=overview_processing.STATE_NAME, payload=payload, updated_at=NOW)
    )
    assert (
        overview_processing.processing_facts(
            SimpleNamespace(get=lambda *_: stored), NOW - timedelta(days=1), NOW
        )
        is None
    )


def test_processing_rollup_bounds_finished_work_but_preserves_old_active_work_and_idle_types():
    def day(offset):
        return str((NOW + timedelta(days=offset)).date())

    payload = {
        "version": overview_processing.VERSION,
        "active": {
            **{
                kind: {"queued": 0, "running": 0, "oldest_queued_at": None}
                for kind in JOB_DEFINITIONS
            },
            "analysis": {
                "queued": 3,
                "running": 1,
                "oldest_queued_at": (NOW - timedelta(days=20)).isoformat(),
            },
        },
        "daily": {
            day(-2): {"analysis": {"completed": 1000, "failed": 1000, "duration_ms": 99999}},
            day(-1): {
                "analysis": {"completed": 4, "failed": 1, "duration_ms": 200, "duration_runs": 2}
            },
            day(0): {"analysis": {"completed": 2, "duration_ms": 300, "duration_runs": 1}},
            day(1): {"analysis": {"completed": 1000}},
        },
    }
    before = copy.deepcopy(payload)
    stored = ReadModelState(name=overview_processing.STATE_NAME, payload=payload, updated_at=NOW)
    results = overview_processing.processing_facts(
        SimpleNamespace(get=lambda *_: stored), NOW - timedelta(days=1), NOW
    )
    indexed = {item["kind"]: item for item in results}
    assert set(indexed) == set(JOB_DEFINITIONS)
    assert indexed["analysis"] == {
        "kind": "analysis",
        "queued": 3,
        "running": 1,
        "oldest_queued_at": (NOW - timedelta(days=20)).isoformat(),
        "completed": 6,
        "failed": 1,
        "duration_ms": 500,
        "duration_runs": 3,
    }
    assert indexed["ingestion"]["completed"] == indexed["ingestion"]["duration_ms"] == 0
    assert indexed["ingestion"]["queued"] == indexed["ingestion"]["running"] == 0
    assert indexed["ingestion"]["oldest_queued_at"] is None
    assert stored.payload == before


@pytest.mark.parametrize(
    "group,days,expected",
    [("daily", 7, 7), ("daily", 90, 90), ("attention", 90, 1), ("blockers", 7, 1)],
)
def test_report_identity_shares_only_range_independent_panels(group, days, expected):
    assert projections.report_key(group, days) == (group, expected)


@pytest.mark.parametrize("group,days", [("unknown", 7), ("daily", 0), ("daily", 91)])
def test_invalid_report_identity_is_rejected_before_recording_demand(group, days):
    with pytest.raises(ValueError, match="Unknown report group or date range"):
        projections.report_key(group, days)


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


@pytest.mark.parametrize("rollup_fails", [False, True])
def test_refresh_worker_isolates_failed_panel_and_rollup_then_publishes_later_claim(
    monkeypatch, rollup_fails
):
    factory, calls = object(), []
    claims = iter([("sources", 7, uuid.uuid4()), ("daily", 7, uuid.uuid4()), None])

    def rollup(*args):
        calls.append("rollup")
        if rollup_fails:
            raise TimeoutError("processing unavailable")

    def calculate(factory, group, days, now):
        calls.append(group)
        if group == "sources":
            raise TimeoutError("one slow dataset")
        return panels.OverviewPanel(generated_at=now, days=days)

    failures, published = [], []
    monkeypatch.setattr(overview_processing, "refresh_processing_facts", rollup)
    monkeypatch.setattr(projections, "seed_refreshes", lambda *args: calls.append("seed"))
    monkeypatch.setattr(projections, "claim_refresh", lambda *args: next(claims))
    monkeypatch.setattr(projections, "calculate_report", calculate)
    monkeypatch.setattr(
        projections,
        "fail_refresh",
        lambda factory, claim, now, error: failures.append((claim[0], type(error))),
    )
    monkeypatch.setattr(
        projections,
        "publish_report",
        lambda factory, claim, result, now: published.append((claim[0], result.days)) or True,
    )
    monkeypatch.setattr(projections, "utcnow", lambda: NOW)

    assert projections.refresh_overview_projections(factory, now=NOW, limit=4) == 1
    assert calls == ["rollup", "seed", "sources", "daily"]
    assert failures == [("sources", TimeoutError)] and published == [("daily", 7)]


def test_refresh_worker_obeys_batch_bound_even_when_claims_keep_arriving(monkeypatch):
    calls = []
    monkeypatch.setattr(overview_processing, "refresh_processing_facts", lambda *_: False)
    monkeypatch.setattr(projections, "seed_refreshes", lambda *_: pytest.fail("seeding disabled"))
    monkeypatch.setattr(projections, "claim_refresh", lambda *_: ("daily", 7, uuid.uuid4()))
    monkeypatch.setattr(
        projections,
        "calculate_report",
        lambda *args: panels.OverviewPanel(generated_at=NOW, days=7),
    )
    monkeypatch.setattr(projections, "publish_report", lambda *args: calls.append(args[1]) or False)
    assert projections.refresh_overview_projections(object(), limit=2, now=NOW, seed=False) == 0
    assert len(calls) == 2  # Lost publication ownership does not count as a published report.


@pytest.mark.parametrize("limit", [0, 101])
def test_refresh_worker_rejects_unbounded_batches_before_touching_storage(limit):
    with pytest.raises(ValueError, match="between 1 and 100"):
        projections.refresh_overview_projections(object(), limit=limit)
