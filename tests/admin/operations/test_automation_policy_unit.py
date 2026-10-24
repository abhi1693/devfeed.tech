"""Recovery commands keep editorial gates; reporting composes already aggregated facts."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import automation, overview_projection
from devfeed_core.inference_metrics import InferenceCharts
from devfeed_core.models import ArticleAnalysisJob, ArticleEnrichmentJob
from devfeed_core.pipeline_metrics import PipelineThroughput
from devfeed_core.topic_decision_metrics import TopicDecisionMetrics

from tests.support.admin_editorial import operator_api
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW, configuration, job


@pytest.fixture
def recovery(processing_case, monkeypatch):
    article, evidence = processing_case
    article.review_status = "pending"
    monkeypatch.setattr(automation, "record", lambda *args, **kw: article)
    authority = Mock(return_value=[article.origins[0].source_id])
    monkeypatch.setattr(automation, "approved_sources", authority)
    monkeypatch.setattr(automation, "lock_topics", Mock())
    with operator_api(automation) as api:
        api.article, api.evidence, api.authority = article, evidence, authority
        yield api


@pytest.mark.parametrize("action", ["enrich", "analyze"])
def test_explicit_recovery_queues_only_the_requested_stage_without_publishing(
    recovery, monkeypatch, action
):
    api = recovery
    monkeypatch.setattr(automation, "get_settings", lambda: configuration(ai_enabled=True))
    requested = job(
        ArticleEnrichmentJob if action == "enrich" else ArticleAnalysisJob,
        article_id=api.article.id,
    )
    command = Mock(return_value=requested)
    other = Mock()
    monkeypatch.setattr(
        automation,
        "request_article_enrichment" if action == "enrich" else "request_analysis",
        command,
    )
    monkeypatch.setattr(
        automation,
        "request_analysis" if action == "enrich" else "request_article_enrichment",
        other,
    )
    response = api.client.post(
        f"/v1/admin/automation/articles/{api.article.id}/{action}", json={"expected_revision": 2}
    )
    assert response.status_code == 200 and response.json() == {
        "status": "queued",
        "job_id": str(requested.id),
        "decision": None,
    }
    command.assert_called_once_with(api.session, api.article.id)
    other.assert_not_called()
    assert (
        api.article.review_status == "pending" and api.article.publication_status == "unpublished"
    )
    api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "barrier", ["source", "revision", "approved", "rejected", "published", "disabled-ai"]
)
def test_recovery_refuses_missing_authority_or_changed_editorial_state(
    recovery, monkeypatch, barrier
):
    api = recovery
    monkeypatch.setattr(
        automation, "get_settings", lambda: configuration(ai_enabled=barrier != "disabled-ai")
    )
    if barrier == "source":
        api.authority.return_value = []
    elif barrier in {"approved", "rejected"}:
        api.article.review_status = barrier
    elif barrier == "published":
        api.article.review_status = "approved"
        api.article.publication_status, api.article.published_to_feed_at = "published", NOW
    command = Mock()
    monkeypatch.setattr(automation, "request_analysis", command)
    response = api.client.post(
        f"/v1/admin/automation/articles/{api.article.id}/analyze",
        json={"expected_revision": 1 if barrier == "revision" else 2},
    )
    assert response.status_code == 409
    assert (
        "approved source"
        if barrier == "source"
        else "Enable AI"
        if barrier == "disabled-ai"
        else "Article changed"
    ) in response.json()["detail"]
    command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("barrier", ["active", "missing", None])
def test_evaluation_waits_for_settled_analysis_and_returns_domain_decision_without_overriding_it(
    recovery, monkeypatch, barrier
):
    api = recovery
    active = (
        job(ArticleAnalysisJob, article_id=api.article.id, status="running")
        if barrier == "active"
        else None
    )
    api.session.scalar.side_effect = (
        [active.id]
        if active
        else [None, None if barrier == "missing" else api.evidence["analysis"]]
    )
    decision = {"status": "blocked", "blockers": ["manual_review_required"]}
    command = Mock(return_value=decision)
    monkeypatch.setattr(automation, "apply_publication_policy", command)
    response = api.client.post(
        f"/v1/admin/automation/articles/{api.article.id}/evaluate", json={"expected_revision": 2}
    )
    assert response.status_code == (409 if barrier else 200)
    if barrier:
        assert ("Wait for active analysis" if active else "Analyze the article") in response.json()[
            "detail"
        ]
        command.assert_not_called()
        api.session.commit.assert_not_called()
    else:
        assert response.json() == {"status": "blocked", "job_id": None, "decision": decision}
        command.assert_called_once_with(api.session, api.article, api.evidence["analysis"])
        api.session.commit.assert_called_once_with()


def test_compact_blockers_keep_overlapping_causes_and_original_recovery_actions(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()

    def cause(code, action, ids):
        return automation.AutomationBlocker(
            code=code,
            label=code.replace("_", " "),
            count=len(ids),
            action=action,
            targets=[
                automation.RecoveryTarget(
                    id=value, title="Source article", kind="article", revision=2
                )
                for value in ids
            ],
        )

    reader = Mock(
        return_value=[
            cause("insufficient_source_text", "enrich", [first]),
            cause("analysis_failed", "analyze", [first, second]),
        ]
    )
    monkeypatch.setattr(automation, "automation_blockers", reader)
    session = object()
    result = automation.automation_blocker_rows(session, [first, second])
    assert result == {
        first: [
            {
                "code": "insufficient_source_text",
                "label": "insufficient source text",
                "action": "enrich",
            },
            {"code": "analysis_failed", "label": "analysis failed", "action": "analyze"},
        ],
        second: [{"code": "analysis_failed", "label": "analysis failed", "action": "analyze"}],
    }
    reader.assert_called_once_with(
        session, [first, second], include_proposals=False, target_limit=2
    )


@pytest.mark.parametrize("count", [0, 201])
def test_compact_blocker_projection_skips_empty_batches_and_rejects_unbounded_work(
    monkeypatch, count
):
    reader = Mock()
    monkeypatch.setattr(automation, "automation_blockers", reader)
    if count:
        with pytest.raises(ValueError, match="at most 200"):
            automation.automation_blocker_rows(object(), [uuid.uuid4() for _ in range(count)])
    else:
        assert automation.automation_blocker_rows(object(), []) == {}
    reader.assert_not_called()


def test_completed_token_series_keeps_idle_days_and_combines_all_three_job_kinds():
    rows = [
        {
            "date": (NOW - timedelta(days=2)).date(),
            "kind": "article_analysis",
            "tokens": 100,
            "reported": 1,
            "unreported": 1,
            "duration": 200,
        },
        {
            "date": NOW.date(),
            "kind": "topic_analysis",
            "tokens": 30,
            "reported": 1,
            "unreported": 0,
            "duration": 50,
        },
        {
            "date": NOW.date(),
            "kind": "research_verification",
            "tokens": 20,
            "reported": 1,
            "unreported": 0,
            "duration": 25,
        },
    ]
    session = SimpleNamespace(execute=lambda _: SimpleNamespace(mappings=lambda: rows))
    points, duration = automation.analysis_token_activity(session, NOW - timedelta(days=2), NOW)
    assert [point.date for point in points] == [
        str((NOW - timedelta(days=2)).date()),
        str((NOW - timedelta(days=1)).date()),
        str(NOW.date()),
    ]
    assert points[0].article_analysis == 100 and points[0].unreported_runs == 1
    assert points[1].article_analysis == points[1].topic_analysis == points[1].reported_runs == 0
    assert points[2].topic_analysis == 30 and points[2].research_verification == 20
    assert points[2].reported_runs == 2 and duration == 275


def test_completed_token_query_streams_bounded_usage_with_once_per_row_validation():
    from sqlalchemy.dialects import postgresql

    statements = []

    def execute(statement):
        statements.append(statement)
        return SimpleNamespace(mappings=lambda: [])

    automation.analysis_token_activity(
        SimpleNamespace(execute=execute), NOW - timedelta(days=7), NOW
    )
    assert len(statements) == 1
    for branch in statements[0].selects:
        usage = branch.get_final_froms()[0]
        raw_usage = usage.element.get_final_froms()[0]
        # Keep the computed date visible across both projection boundaries;
        # grouping a timestamp expression loses its daily cardinality estimate.
        projected_day = branch.selected_columns["date"].element
        assert getattr(projected_day, "table", None) is usage
        assert getattr(usage.element.selected_columns["date"], "table", None) is raw_usage
        assert len(usage.c) == len(raw_usage.c) == 3
        assert "finished_at" not in usage.c and "finished_at" not in raw_usage.c
        assert "raw_tokens" not in usage.c
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    # Literal OFFSET 0 fences preserve both streaming projections in generic
    # plans, without introducing a materialized working set or runtime limits.
    assert sql.count("OFFSET 0") == 6
    assert "MATERIALIZED" not in sql
    for table in ("article_analysis_jobs", "topic_analysis_jobs", "research_verification_jobs"):
        assert sql.count(f"{table}.usage ->>") == 1
        assert f"{table}.finished_at >=" in sql
        assert f"{table}.finished_at <=" in sql
        assert f"{table}.status IN" in sql
    assert sql.count("left(") == 3
    assert sql.count("CASE WHEN") == 3


@pytest.mark.parametrize(
    "published,automatic,median,percent", [(0, 0, None, None), (3, 2, 120.0, 66.7)]
)
def test_publication_metrics_distinguish_empty_history_from_zero_automatic_publication(
    published, automatic, median, percent
):
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(one=lambda: (published, automatic, median))
    )
    assert automation.publication_automation(session, NOW - timedelta(days=7), NOW) == {
        "published_in_window": published,
        "published_without_intervention": automatic,
        "automatic_publication_percent": percent,
        "median_ingestion_to_publication_seconds": median,
    }


@pytest.mark.parametrize("projected", [False, True])
def test_automation_overview_composes_one_consistent_window_and_selects_correct_blocker_reader(
    monkeypatch, projected
):
    session, start = object(), NOW - timedelta(days=7)
    blockers = [
        automation.AutomationBlocker(
            code="manual_review_required", label="Manual review", count=1, action=None, targets=[]
        )
    ]
    chosen, unused = (
        Mock(return_value=blockers),
        Mock(side_effect=AssertionError("Wrong blocker reader")),
    )
    monkeypatch.setattr(overview_projection, "projected_blockers", chosen if projected else unused)
    monkeypatch.setattr(automation, "automation_blockers", unused if projected else chosen)
    publication = {
        "published_in_window": 4,
        "published_without_intervention": 3,
        "automatic_publication_percent": 75.0,
        "median_ingestion_to_publication_seconds": 90.0,
    }
    publishers = Mock(return_value=publication)
    tokens = [
        automation.AnalysisTokenDay(
            date=str(NOW.date()),
            article_analysis=100,
            topic_analysis=30,
            research_verification=20,
            reported_runs=3,
            unreported_runs=1,
        )
    ]
    token_reader = Mock(return_value=(tokens, 500))
    monkeypatch.setattr(automation, "publication_automation", publishers)
    monkeypatch.setattr(automation, "analysis_token_activity", token_reader)
    for name, result in [
        ("decision_metrics", TopicDecisionMetrics()),
        ("inference_charts", InferenceCharts()),
        ("pipeline_throughput", PipelineThroughput()),
    ]:
        monkeypatch.setattr(automation, name, Mock(return_value=result))
    result = automation.automation_metrics(session, start, NOW, projected=projected)
    chosen.assert_called_once_with(session)
    unused.assert_not_called()
    publishers.assert_called_once_with(session, start, NOW)
    token_reader.assert_called_once_with(session, start, NOW)
    assert result.blockers == blockers and result.analysis_tokens == 150
    assert result.analysis_duration_ms == 500 and result.usage_reported_runs == 3
    assert result.usage_unreported_runs == 1 and result.automatic_publication_percent == 75
