"""Saved report serving maps reusable facts without returning to raw history.

Supplied rows are database aggregation boundaries; PostgreSQL arithmetic, capture
and replay retain their independent integration tests.
"""

import uuid
from datetime import timedelta, timezone
from decimal import Decimal

import pytest
from devfeed_admin_api import automation, overview_insights
from sqlalchemy.dialects import postgresql

from tests.support.source_readiness import NOW


class Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def all(self):
        return self.value

    def mappings(self):
        return self.value

    def __iter__(self):
        return iter(self.value)


class FactSession:
    def __init__(self, results, *, median=None):
        self.info = {"reporting_facts": True}
        self.results = iter(results)
        self.statements = []
        self.median = median

    def execute(self, query):
        self.statements.append(query)
        return Result(next(self.results))

    def scalar(self, query):
        self.statements.append(query)
        sql = str(query)
        if "reporting_fact_states" in sql:
            return 5
        if "reporting_dirty" in sql:
            return None
        assert "percentile_cont" in sql
        return self.median


def rendered(session):
    return [str(query.compile(dialect=postgresql.dialect())) for query in session.statements]


@pytest.mark.parametrize("empty", [False, True])
def test_saved_job_tokens_fill_utc_days_and_keep_unknown_runs_and_cumulative_durations(empty):
    start = (NOW - timedelta(days=2)).replace(hour=23, minute=30)
    end = start + timedelta(days=2)
    # The caller's calendar date differs from the report's UTC completion date.
    local_start = start.astimezone(timezone(timedelta(hours=14)))
    assert local_start.date() != start.date()
    first, last = start.date(), end.date()
    supplied = [
        [
            {
                "day": first,
                "tokens": Decimal(10),
                "reported": Decimal(1),
                "unreported": Decimal(2),
                "duration": Decimal(90),
            },
            {
                "day": last,
                "tokens": Decimal(0),
                "reported": Decimal(2),
                "unreported": Decimal(1),
                "duration": Decimal(30),
            },
        ],
        [
            {
                "day": last,
                "tokens": Decimal(7),
                "reported": Decimal(1),
                "unreported": Decimal(0),
                "duration": Decimal(12),
            }
        ],
        [
            {
                "day": first,
                "tokens": Decimal(3),
                "reported": Decimal(1),
                "unreported": Decimal(0),
                "duration": Decimal(5),
            }
        ],
    ]
    session = FactSession([[], [], []] if empty else supplied)
    days, duration = automation.analysis_token_activity(session, local_start, end)
    assert [day.date for day in days] == [str(first + timedelta(days=index)) for index in range(3)]
    assert days[1].article_analysis == days[1].topic_analysis == days[1].research_verification == 0
    if empty:
        assert duration == 0
        assert all(day.reported_runs == day.unreported_runs == 0 for day in days)
    else:
        assert days[0].model_dump() == {
            "date": str(first),
            "article_analysis": 10,
            "topic_analysis": 0,
            "research_verification": 3,
            "reported_runs": 2,
            "unreported_runs": 2,
        }
        # Two reported zero-token runs retain their distinction from a missing usage run.
        assert days[2].article_analysis == 0 and days[2].topic_analysis == 7
        assert days[2].reported_runs == 3 and days[2].unreported_runs == 1
        assert duration == 137
    queries = rendered(session)
    assert len([query for query in queries if "reporting_daily_facts" in query]) == 3
    assert all(
        "article_analysis_jobs" not in query and "inference_calls" not in query for query in queries
    )


@pytest.mark.parametrize("published,automatic,median", [(0, 0, None), (8, 3, Decimal("4.5"))])
def test_saved_publication_totals_keep_exact_latency_and_empty_denominator(
    published, automatic, median
):
    session = FactSession([(Decimal(published), Decimal(automatic))], median=median)
    value = automation.publication_automation(session, NOW - timedelta(days=7), NOW)
    assert value == {
        "published_in_window": published,
        "published_without_intervention": automatic,
        "automatic_publication_percent": 37.5 if published else None,
        "median_ingestion_to_publication_seconds": median,
    }
    queries = rendered(session)
    assert any("reporting_daily_facts" in query for query in queries)
    percentile = next(query for query in queries if "percentile_cont" in query)
    assert "reporting_article_facts.latency_seconds" in percentile
    assert "reporting_daily_facts" not in percentile
    assert all("article_reviews" not in query for query in queries)


@pytest.mark.parametrize("empty", [False, True])
def test_saved_topic_coverage_preserves_window_counts_and_all_time_latest_publication(empty):
    identifier = uuid.uuid4()
    row = {
        "id": identifier,
        "name": "Python",
        "logo_url": None,
        "managed_logo": {},
        "followers": 3,
        "inferred_users": 5,
        "publications": 0,
        "last_published_at": NOW - timedelta(days=100),
    }
    session = FactSession([[] if empty else [row]])
    result = overview_insights.interest_coverage(session, NOW - timedelta(days=7), NOW)
    assert len(result) == (0 if empty else 1)
    if not empty:
        assert result[0].id == identifier and result[0].publications == 0
        assert result[0].followers == 3 and result[0].inferred_users == 5
        assert result[0].last_published_at == NOW - timedelta(days=100)
    query = session.statements[-1]
    assert query._limit_clause.value == 10
    latest = str(query.selected_columns.last_published_at.element)
    assert "reporting_article_topics" in latest and "reporting_article_facts.published" in latest
    assert "published_at >=" not in latest and "published_at <=" not in latest
    assert "article_topics" not in str(query).replace("reporting_article_topics", "")


@pytest.mark.parametrize("empty", [False, True])
def test_saved_source_performance_preserves_window_total_outside_visible_ranked_rows(empty):
    identifier, failing_id = uuid.uuid4(), uuid.uuid4()
    row = {
        "id": identifier,
        "name": "Source",
        "logo_url": None,
        "discovered": 12,
        "published": 6,
        "followers": 3,
        "fetch_failures": 2,
        "consecutive_failures": 0,
        "last_success_at": NOW,
        "total": Decimal(15),
    }
    failed = {
        **row,
        "id": failing_id,
        "name": "Failing source",
        "published": 0,
        "fetch_failures": 7,
        "consecutive_failures": 2,
        "last_success_at": None,
    }
    session = FactSession([[], []] if empty else [[row], [failed]])
    ranked, failures, total = overview_insights.sources_performance(
        session, NOW - timedelta(days=7), NOW
    )
    if empty:
        assert ranked == failures == [] and total == 0
    else:
        assert ranked[0].published == 6 and ranked[0].discovered == 12
        assert total == 15  # The full source total exceeds the visible ranked cohort.
        assert failures[0].id == failing_id and failures[0].fetch_failures == 7
        assert failures[0].last_success_at is None
    query, failing_query = session.statements[-2:]
    assert query._limit_clause.value == 12 and failing_query._limit_clause.value == 5
    assert "OVER ()" in str(query)
    assert all(
        "ingestion_jobs" not in query and "article_origins" not in query
        for query in rendered(session)
    )


def test_saved_insights_compose_exact_latency_with_daily_history_and_job_means(monkeypatch):
    previous, today = NOW.date() - timedelta(days=2), NOW.date()
    daily = {
        previous: {"added": 3, "published": 2, "opens": None, "accounts": 1},
        today: {"added": 5, "published": 6, "opens": 4, "accounts": 2},
    }
    monkeypatch.setattr(overview_insights, "read_daily_metrics", lambda *_: daily)
    monkeypatch.setattr(overview_insights, "sources_performance", lambda *_: ([], [], 9))
    monkeypatch.setattr(overview_insights, "interest_coverage", lambda *_: [])
    monkeypatch.setattr(
        overview_insights,
        "reader_adoption",
        lambda *_: overview_insights.OverviewAdoption(accounts=7),
    )
    monkeypatch.setattr(
        overview_insights,
        "personalization",
        lambda *_: overview_insights.OverviewPersonalization(ready=3),
    )
    jobs = [
        overview_insights.OverviewProcessing(
            kind=kind,
            queued=0,
            running=0,
            completed=1,
            failed=0,
            oldest_queued_at=None,
            duration_ms=duration,
            duration_runs=runs,
        )
        for kind, duration, runs in [
            ("analysis", 5000, 2),
            ("topic-analysis", 1000, 1),
            ("ingestion", 999999, 1),
        ]
    ]
    monkeypatch.setattr(overview_insights, "processing", lambda *_: jobs)
    session = FactSession(
        [(Decimal(3), Decimal(8), Decimal(9)), [], [("articles", NOW - timedelta(days=5))]]
    )
    value = overview_insights.overview_insights(session, 2, NOW)
    assert value.publications.current == 6 and value.publications.previous == 2
    assert value.opens.current == 4 and value.opens.previous is None
    assert value.publication_seconds.current == 3 and value.publication_seconds.previous == 8
    assert value.publication_p90_seconds == 9
    assert value.analysis_average_seconds == 2
    assert value.source_publications_total == 9 and value.adoption.accounts == 7
    assert value.personalization.ready == 3 and value.processing == jobs
    assert value.oldest_review_at == {"articles": NOW - timedelta(days=5)}
    assert len(value.reader_activity) == 1 and value.reader_activity[0].added == 5
    assert any("reporting_article_facts.latency_seconds" in query for query in rendered(session))
