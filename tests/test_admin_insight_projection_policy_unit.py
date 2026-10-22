"""Private insight projections preserve measured facts, issue meaning and report windows.

Inputs are already aggregated read results. Query eligibility and relationship
cardinality are database integration concerns, not simulated by these fixtures.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import overview_insights as insights
from devfeed_core import logos
from devfeed_core.job_definitions import JOB_DEFINITIONS
from test_admin_overview_contracts_unit import daily_values
from test_source_readiness_unit import NOW, configuration


def result(*, rows=(), one=None):
    return SimpleNamespace(mappings=lambda: rows, all=lambda: rows, one=lambda: one)


def test_reader_adoption_projects_account_counts_without_interpreting_them_as_event_totals():
    session = SimpleNamespace(execute=lambda _: result(one=(12, 7, 4, 3)))
    assert insights.reader_adoption(session).model_dump() == dict(
        accounts=12, liking=7, following_topics=4, following_sources=3
    )


def test_personalization_issues_explain_overdue_expired_and_empty_ready_feeds_separately():
    ids = [uuid.uuid4() for _ in range(3)]
    selected = iter(
        [
            result(one=(8, 2, 3, 1, 4, 2, 1)),
            result(
                rows=[
                    {
                        "id": ids[0],
                        "name": "Overdue reader",
                        "status": "expired",
                        "overdue": True,
                        "next_refresh_at": NOW - timedelta(minutes=20),
                    },
                    {
                        "id": ids[1],
                        "name": "Expired reader",
                        "status": "expired",
                        "overdue": False,
                        "next_refresh_at": NOW,
                    },
                    {
                        "id": ids[2],
                        "name": "Ready reader with interests",
                        "status": "ready",
                        "overdue": False,
                        "next_refresh_at": NOW + timedelta(minutes=10),
                    },
                ]
            ),
            result(rows=[{"reason": "followed_topic", "users": 5, "recommendations": 12}]),
        ]
    )
    report = insights.personalization(SimpleNamespace(execute=lambda _: next(selected)), NOW)
    assert (report.ready, report.refreshing, report.expired, report.pending, report.not_needed) == (
        8,
        2,
        3,
        1,
        4,
    )
    assert report.overdue == 2 and report.empty_with_interests == 1
    assert [(issue.id, issue.issue) for issue in report.issues] == list(
        zip(ids, ["Refresh overdue", "Expired feed", "No prepared recommendations"], strict=True)
    )
    assert report.reasons[0].model_dump() == {
        "reason": "followed_topic",
        "users": 5,
        "recommendations": 12,
    }


@pytest.mark.parametrize("managed", [False, True])
def test_interest_coverage_serves_managed_logo_and_preserves_inferred_and_explicit_interest(
    monkeypatch, managed
):
    monkeypatch.setattr(
        logos, "get_settings", lambda: configuration(image_public_url="https://images.example")
    )
    row = {
        "id": uuid.uuid4(),
        "name": "PostgreSQL",
        "logo_url": "https://upstream.example/logo.png",
        "managed_logo": {
            "source_url": "https://upstream.example/logo.png",
            "version": "v1",
            "variants": [
                {"key": f"logos/postgresql/{size}.webp", "width": size} for size in (32, 64, 96)
            ],
        }
        if managed
        else {},
        "followers": 7,
        "inferred_users": 11,
        "publications": 4,
        "last_published_at": NOW - timedelta(hours=1),
    }
    session = SimpleNamespace(execute=lambda _: result(rows=[row]))
    [report] = insights.interest_coverage(session, NOW - timedelta(days=7), NOW)
    assert report.logo_url == (
        "https://images.example/logos/postgresql/64.webp" if managed else None
    )
    assert (report.followers, report.inferred_users, report.publications) == (7, 11, 4)
    assert report.last_published_at == row["last_published_at"]
    assert "managed_logo" not in report.model_dump()


def source_row(name, published, **changes):
    return dict(
        id=uuid.uuid4(),
        name=name,
        logo_url=None,
        discovered=published + 4,
        published=published,
        followers=3,
        fetch_failures=1,
        consecutive_failures=1,
        last_success_at=NOW - timedelta(hours=1),
        **changes,
    )


@pytest.mark.parametrize("empty", [False, True])
def test_source_performance_keeps_global_publication_total_and_failure_rows_independent(empty):
    ranked = (
        []
        if empty
        else [
            {**source_row("Primary feed", 7), "total": 45},
            {**source_row("Another feed", 3), "total": 45},
        ]
    )
    failing = {
        **source_row("Failing feed", 0),
        "consecutive_failures": 4,
        "fetch_failures": 6,
        "last_success_at": None,
    }
    selected = iter([result(rows=ranked), result(rows=[failing])])
    shown, errors, total = insights.sources_performance(
        SimpleNamespace(execute=lambda _: next(selected)), NOW - timedelta(days=7), NOW
    )
    assert total == (0 if empty else 45)
    assert [row.published for row in shown] == ([] if empty else [7, 3])
    assert errors[0].id == failing["id"] and errors[0].consecutive_failures == 4
    assert errors[0].last_success_at is None
    assert all("total" not in row.model_dump() for row in shown)


def test_processing_legacy_read_converts_all_preaggregated_job_types_and_keeps_old_queue_age():
    rows = [
        dict(
            kind=kind,
            queued=2 if kind == "analysis" else 0,
            running=0,
            completed=4 if kind == "analysis" else 0,
            failed=1 if kind == "analysis" else 0,
            duration_ms=900 if kind == "analysis" else 0,
            duration_runs=3 if kind == "analysis" else 0,
            oldest_queued_at=NOW - timedelta(days=100) if kind == "analysis" else None,
        )
        for kind in JOB_DEFINITIONS
    ]
    session = SimpleNamespace(get=lambda *_: None, execute=lambda _: result(rows=rows))
    report = insights.processing(session, NOW - timedelta(days=7), NOW)
    assert {row.kind for row in report} == set(JOB_DEFINITIONS)
    analysis = next(row for row in report if row.kind == "analysis")
    assert analysis.completed == 4 and analysis.failed == 1
    assert analysis.oldest_queued_at == NOW - timedelta(days=100)
    assert (analysis.duration_ms, analysis.duration_runs) == (900, 3)


@pytest.mark.parametrize("measured", [False, True])
def test_full_insights_compare_equal_windows_and_compute_weighted_ai_duration(
    monkeypatch, measured
):
    daily = {
        (NOW - timedelta(days=3)).date(): daily_values(published=8, opens=None),
        (NOW - timedelta(days=2)).date(): daily_values(published=2, opens=3),
        (NOW - timedelta(days=1)).date(): daily_values(published=5, opens=7),
        NOW.date(): daily_values(published=3, opens=2),
    }
    read = Mock(return_value=daily)
    monkeypatch.setattr(insights, "read_daily_metrics", read)
    popular = {"id": uuid.uuid4(), "title": "Measured reader article", "opens": 9, "likes": 2}
    oldest = [
        ("articles", NOW - timedelta(days=4)),
        ("sources", None),
        ("topics", NOW - timedelta(days=2)),
        ("relationships", None),
    ]
    selected = iter([result(one=(20.0, 40.0, 90.0)), result(rows=[popular]), result(rows=oldest)])
    session = SimpleNamespace(execute=lambda _: next(selected))
    jobs = [
        insights.OverviewProcessing(
            kind=kind,
            queued=0,
            running=0,
            completed=3,
            failed=0,
            oldest_queued_at=None,
            duration_ms=duration if measured else 0,
            duration_runs=runs if measured else 0,
        )
        for kind, duration, runs in [
            ("analysis", 2000, 2),
            ("topic-analysis", 4000, 1),
            ("images", 90000, 3),
        ]
    ]
    monkeypatch.setattr(insights, "processing", lambda *_: jobs)
    monkeypatch.setattr(insights, "sources_performance", lambda *_: ([], [], 17))
    monkeypatch.setattr(insights, "interest_coverage", lambda *_: [])
    monkeypatch.setattr(
        insights, "reader_adoption", lambda *_: insights.OverviewAdoption(accounts=9, liking=3)
    )
    monkeypatch.setattr(
        insights, "personalization", lambda *_: insights.OverviewPersonalization(ready=5)
    )
    report = insights.overview_insights(session, 2, NOW)
    assert read.call_args.args == (session, NOW.replace(hour=0) - timedelta(days=3), NOW)
    assert report.publications.model_dump() == {"current": 8, "previous": 10}
    assert report.opens.model_dump() == {"current": 9, "previous": None}
    assert report.analysis_average_seconds == (2.0 if measured else None)
    assert report.publication_seconds.model_dump() == {"current": 20, "previous": 40}
    assert report.publication_p90_seconds == 90
    assert report.top_articles[0].id == popular["id"] and report.top_articles_days == 2
    assert report.source_publications_total == 17 and report.adoption.accounts == 9
    assert report.personalization.ready == 5 and report.oldest_review_at == dict(oldest)
    assert len(report.reader_activity) == 2
