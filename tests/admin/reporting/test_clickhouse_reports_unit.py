"""Overview composes bounded bucket generations without reading operational history."""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from unittest.mock import AsyncMock

import pytest
from devfeed_admin_api.analytics_reports import ANALYTICAL_GROUPS, Reports, _gather
from devfeed_admin_api.overview_windows import ReportWindow, parse_report_window, recent_window
from devfeed_core.analytics import AnalyticsUnavailable

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
CAPTURED = NOW - timedelta(days=100)
SOURCE = "00000000-0000-0000-0000-000000000001"
TOPIC = "00000000-0000-0000-0000-000000000002"
BUCKET = "2026-10-26T00:00:00Z"


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return run


class Client:
    def __init__(self, results=None):
        self.results = results or {}
        self.queries = []

    async def query(self, sql, parameters=None):
        self.queries.append((sql, parameters))
        name = sql.split("overview:", 1)[1].split(" ", 1)[0]
        if name == "inference-ready":
            return deepcopy(self.results.get(name, [{"complete": 1}]))
        return deepcopy(self.results.get(name, []))


def reports(client=None, *, days=30, now=NOW, captured=CAPTURED):
    return Reports(client or Client(), recent_window(days, now), now, capture_started_at=captured)


def inference_row(**changes):
    return {
        "dimension": "activity",
        "name": "",
        "chart_bucket": BUCKET,
        "calls": 3,
        "failed": 1,
        "unreported": 1,
        "input_tokens": 2**80,
        "output_tokens": 300,
        "cached_input_tokens": 100,
        "reasoning_tokens": 50,
        "web_searches": 2,
        "percentiles": [2_000, 8_000],
        **changes,
    }


@pytest.mark.parametrize("section", sorted(ANALYTICAL_GROUPS))
@run_async
async def test_empty_sections_still_supply_their_typed_contract_and_bounded_chart(section):
    client = Client()
    result = await reports(client).section(section)
    assert result.generated_at == NOW
    assert result.days == 30
    assert result.data.section == section
    assert client.queries
    for sql, params in client.queries:
        assert "analytics_" in sql
        assert params["start"] == datetime(2026, 9, 27, tzinfo=UTC)
        assert params["end"] in {
            datetime(2026, 10, 27, tzinfo=UTC),
            NOW.replace(minute=0) + timedelta(hours=1),
        }
        assert "_versions" not in sql
        assert "analytics_fact" not in sql
    if section == "publishing":
        assert result.data.summary.published == 0
        assert result.data.summary.automatic_percent is None
    if section == "audience":
        assert result.data.personalization.ready == 0
        assert all(point.opens == 0 for point in result.data.activity)
    if section == "processing":
        assert not result.data.queues and not result.data.outcomes
        assert all(point.article_analyses == 0 for point in result.data.throughput)
    if section == "inference":
        assert result.data.summary.calls == 0
        assert not result.data.tasks and not result.data.models and not result.data.efforts
    if section == "coverage":
        assert not result.data.sources and not result.data.topics
        assert result.data.source_publications_total == 0


@run_async
async def test_publication_totals_use_the_merged_percentile_and_keep_zero_buckets():
    client = Client(
        {
            "publishing": [
                {
                    "total": 0,
                    "chart_bucket": BUCKET,
                    "published": 5,
                    "automated": 3,
                    "percentiles": [20, 30],
                },
                {"total": 1, "published": 10, "automated": 6, "percentiles": [25, 40]},
            ]
        }
    )
    result = await reports(client).publishing()
    assert result.summary.model_dump() == {
        "published": 10,
        "automated": 6,
        "automatic_percent": 60.0,
        "p50_seconds": 25,
        "p95_seconds": 40,
    }
    assert len(result.activity) == 30
    assert result.activity[-1].published == 5
    assert result.activity[-1].p50_seconds == 20
    assert result.activity[0].published == 0
    assert result.activity[0].p50_seconds is None


@pytest.mark.parametrize("archived", [9, 0])
@run_async
async def test_imported_publications_replace_counts_and_leave_unobserved_automation_unknown(
    archived,
):
    day = "2026-10-24"
    client = Client(
        {
            "publishing": [
                {
                    "total": 0,
                    "chart_bucket": day + "T00:00:00Z",
                    "published": 4,
                    "automated": 3,
                    "percentiles": [20, 30],
                },
                {"total": 1, "published": 4, "automated": 3, "percentiles": [20, 30]},
            ],
            "archive": [{"day": day, "published": archived}],
        }
    )
    result = await reports(client, days=7, captured=NOW).publishing()
    assert len(result.activity) == 7  # A daily-only archive cannot be invented as hours.
    assert result.summary.published == archived
    assert result.summary.automated is None
    assert result.summary.automatic_percent is None
    assert result.summary.p50_seconds is result.summary.p95_seconds is None
    point = result.activity[-3]
    assert point.published == archived
    assert point.automated is point.p50_seconds is point.p95_seconds is None


@run_async
async def test_matching_or_unavailable_archive_does_not_erase_known_publication_evidence():
    client = Client(
        {
            "publishing": [
                {
                    "total": 0,
                    "chart_bucket": BUCKET,
                    "published": 4,
                    "automated": 3,
                    "percentiles": [20, 30],
                },
                {"total": 1, "published": 4, "automated": 3, "percentiles": [20, 30]},
            ],
            "archive": [
                {"day": "2026-10-26", "published": 4},
                {"day": "2026-10-25", "published": None},
                {"day": "2025-01-01", "published": 100},
            ],
        }
    )
    result = await reports(client).publishing()
    assert result.summary.published == 4
    assert result.summary.automated == 3
    assert result.summary.automatic_percent == 75
    assert result.summary.p95_seconds == 30


@run_async
async def test_audience_preserves_unknown_capture_history_and_merges_current_health_shards():
    client = Client(
        {
            "audience": [
                {
                    "chart_bucket": "2026-09-29T00:00:00Z",
                    "opens": 4,
                    "estimated_readers": 2.25,
                    "new_accounts": 1,
                }
            ],
            "feed-health": [{"ready": 8, "refreshing": 2, "pending": None, "overdue": 1}],
            "archive": [
                {"day": "2026-09-28", "opens": 17, "readers": None, "accounts_created": 3},
                {"day": "2026-09-29", "opens": 20, "readers": 11, "accounts_created": None},
                {"day": "2025-01-01", "opens": 1, "readers": 1, "accounts_created": 1},
            ],
        }
    )
    result = await reports(client, days=31, captured=NOW).audience()
    assert result.activity[0].opens is None  # Fixed capture - 29 days, not moving retention.
    assert result.activity[2].opens == 17
    assert result.activity[2].estimated_readers is None
    assert result.activity[2].new_accounts == 3
    assert result.activity[3].opens == 20  # Archive replaces, never doubles.
    assert result.activity[3].estimated_readers == 11
    assert result.activity[3].new_accounts == 1
    assert result.personalization.ready == 8
    assert result.personalization.refreshing == 2
    assert result.personalization.pending == 0
    assert result.personalization.overdue == 1


@run_async
async def test_reader_coverage_is_fixed_even_after_source_retention_moves_on():
    result = await reports(days=90).audience()
    assert len(result.activity) == 90
    assert all(point.opens == 0 and point.estimated_readers == 0 for point in result.activity)


@run_async
async def test_unobservable_raw_audience_counts_do_not_masquerade_as_known_readers():
    client = Client(
        {
            "audience": [
                {
                    "chart_bucket": "2026-09-26T00:00:00Z",
                    "opens": 4,
                    "estimated_readers": 2,
                    "new_accounts": 1,
                }
            ]
        }
    )
    result = await reports(client, days=31, captured=NOW).audience()
    assert result.activity[0].new_accounts == 1
    assert result.activity[0].opens is result.activity[0].estimated_readers is None


@run_async
async def test_processing_combines_useful_output_without_mixing_decision_rows_into_job_outcomes():
    client = Client(
        {
            "job-outcomes": [
                {"kind": "analysis", "completed": 5, "failed": 2, "percentiles": [4, 8]},
                {"kind": "notification", "completed": 1, "failed": 0, "percentiles": None},
            ],
            "throughput": [
                {
                    "chart_bucket": BUCKET,
                    "articles_published": 7,
                    "article_analyses": 4,
                    "topic_decisions": 2,
                }
            ],
        }
    )
    result = await reports(client).processing()
    assert result.throughput[-1].model_dump(exclude={"bucket"}) == {
        "articles_published": 7,
        "article_analyses": 4,
        "topic_decisions": 2,
    }
    assert not result.queues
    assert len(client.queries) == 2
    assert result.outcomes[0].p95_seconds == 8
    assert result.outcomes[1].p50_seconds is None
    outcome_sql = next(sql for sql, _ in client.queries if "overview:job-outcomes" in sql)
    assert "r.kind != 'topic-decision'" in outcome_sql


@run_async
async def test_queue_pressure_reads_only_current_gauges_without_scanning_processing_history():
    client = Client(
        {
            "queues": [
                {
                    "kind": "analysis",
                    "queued": 9,
                    "running": 2,
                    "oldest_queued_at": NOW - timedelta(hours=2),
                }
            ]
        }
    )
    result = await reports(client).section("queues")
    assert result.data.queues[0].queued == 9 and result.data.queues[0].running == 2
    assert result.data.queues[0].oldest_queued_at == NOW - timedelta(hours=2)
    assert not result.data.outcomes and not result.data.throughput
    assert len(client.queries) == 1
    sql = client.queries[0][0]
    assert "analytics_queue_gauges" in sql and "analytics_gauge_manifest" in sql
    assert "analytics_processing_hourly" not in sql and "analytics_publishing_hourly" not in sql


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf")])
@run_async
async def test_nonfinite_latency_is_unavailable_instead_of_invalid_json(bad):
    client = Client({"inference": [inference_row(percentiles=[bad, bad])]})
    result = await reports(client).inference()
    assert result.activity[-1].p50_ms is result.activity[-1].p95_ms is None
    assert "NaN" not in result.model_dump_json()


@run_async
async def test_inference_keeps_int128_totals_exact_and_treats_cached_reasoning_as_subsets():
    breakdown = {
        "name": "Top contributor",
        "calls": 3,
        "failed": 1,
        "unreported": 1,
        "input_tokens": 2**80,
        "output_tokens": 300,
    }
    client = Client(
        {
            "inference": [
                inference_row(),
                inference_row(dimension="summary"),
                *(
                    inference_row(**breakdown, dimension=name)
                    for name in ("tasks", "models", "efforts")
                ),
            ],
        }
    )
    result = await reports(client).inference()
    assert result.summary.input_tokens == 2**80
    assert result.summary.output_tokens == 300
    assert result.summary.cached_input_tokens == 100
    assert result.summary.reasoning_tokens == 50
    assert result.summary.calls == 3
    assert result.summary.failed == result.summary.unreported == 1
    assert result.summary.web_searches == 2
    assert result.activity[-1].p50_ms == 2_000
    assert result.activity[-1].p95_ms == 8_000
    assert (
        result.tasks[0].input_tokens
        == result.models[0].input_tokens
        == result.efforts[0].input_tokens
        == 2**80
    )
    sql = next(sql for sql, _ in client.queries if "overview:inference " in sql)
    assert "rank>8" in sql and "GROUPING SETS" in sql


@run_async
async def test_coverage_preserves_global_total_and_current_labels_in_bounded_top_lists():
    client = Client(
        {
            "sources": [
                {
                    "id": SOURCE,
                    "name": "Publisher",
                    "publications": 5,
                    "consecutive_failures": 2,
                    "total": 9,
                }
            ],
            "topics": [
                {
                    "id": TOPIC,
                    "name": "Python",
                    "followers": 8,
                    "inferred_users": 2,
                    "publications": 4,
                }
            ],
        }
    )
    result = await reports(client).coverage()
    assert result.source_publications_total == 9
    assert result.sources[0].name == "Publisher"
    assert result.sources[0].consecutive_failures == 2
    assert str(result.sources[0].id) == SOURCE
    assert result.topics[0].followers == 8
    assert result.topics[0].inferred_users == 2
    assert result.topics[0].publications == 4
    assert all("LIMIT 10" in sql for sql, _ in client.queries)


@pytest.mark.parametrize("days,expected", [(1, 24), (7, 168), (30, 30), (90, 90)])
def test_range_output_and_partition_bounds_are_fixed(days, expected):
    report = reports(days=days, now=NOW.replace(hour=23, minute=59))
    assert len(report.buckets()) == expected
    assert report.buckets()[0] == report.start
    assert report.buckets()[-1] <= report.now


@pytest.mark.parametrize("days", [0, 3661, -1])
def test_invalid_ranges_do_not_send_a_query(days):
    with pytest.raises(ValueError, match="between 1 and 3660"):
        reports(days=days)


@run_async
async def test_operations_are_not_allowed_through_the_analytical_dispatcher():
    client = Client()
    with pytest.raises(ValueError, match="transactional"):
        await reports(client).section("operations")
    assert not client.queries


@run_async
async def test_hourly_ranges_do_not_replace_observed_hours_with_imported_daily_counts():
    client = Client(
        {
            "archive": [
                {
                    "day": "2026-10-26",
                    "published": 99,
                    "opens": 99,
                    "readers": 99,
                    "accounts_created": 99,
                }
            ]
        }
    )
    report = reports(client, days=1)
    publishing, audience = await asyncio.gather(report.publishing(), report.audience())
    assert len(publishing.activity) == len(audience.activity) == 13
    assert publishing.summary.published == 0
    assert audience.activity[-1].opens == 0


@run_async
async def test_sections_propagate_clickhouse_errors_instead_of_publishing_zero_reports():
    client = Client()
    client.query = AsyncMock(side_effect=AnalyticsUnavailable("offline"))
    with pytest.raises(AnalyticsUnavailable, match="offline"):
        await reports(client).section("coverage")


@pytest.mark.parametrize("cancel_parent", [False, True])
@run_async
async def test_group_failure_or_cancellation_finishes_cancelled_siblings(cancel_parent):
    started, finished = asyncio.Event(), asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    async def failure():
        await started.wait()
        raise AnalyticsUnavailable("offline")

    task = asyncio.create_task(_gather(slow()) if cancel_parent else _gather(slow(), failure()))
    await started.wait()
    if cancel_parent:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel_parent else AnalyticsUnavailable):
        await task
    assert finished.is_set()


@run_async
async def test_each_report_limits_native_query_fanout_to_two():
    active = maximum = 0

    class DelayedClient(Client):
        async def query(self, sql, parameters=None):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0)
                return []
            finally:
                active -= 1

    report = reports(DelayedClient())
    await asyncio.gather(*(report.query("SELECT 1") for _ in range(8)))
    assert maximum == 2
    assert active == 0


@run_async
async def test_report_timeout_cancels_native_work(monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda _limit: real_timeout(0.001))
    cancelled = asyncio.Event()

    class WaitingClient(Client):
        async def query(self, sql, parameters=None):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    with pytest.raises(TimeoutError):
        await reports(WaitingClient()).section("publishing")
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "lower,upper,start,end",
    [
        (None, None, "2026-09-27", "2026-10-27"),
        ("now/d", "now", "2026-10-26", "2026-10-27"),
        ("now-1d/d", "now-1d/d", "2026-10-25", "2026-10-26"),
        ("now-2d/d", "now", "2026-10-24", "2026-10-27"),
        ("now-3M", "now", "2026-07-26", "2026-10-27"),
        ("now-6M", "now", "2026-04-26", "2026-10-27"),
        ("now-9M", "now", "2026-01-26", "2026-10-27"),
        ("now/y", "now", "2026-01-01", "2026-10-27"),
        ("now-1y/y", "now-1y/y", "2025-01-01", "2026-01-01"),
        ("2024-11-01", "2024-11-16", "2024-11-01", "2024-11-17"),
        ("now-2y", "now", "2024-10-26", "2026-10-27"),
        ("now-1w/w", "now-1w/w", "2026-10-19", "2026-10-26"),
        ("now-1M/M", "now-1M/M", "2026-09-01", "2026-10-01"),
        ("2024-02-29", "2024-02-29", "2024-02-29", "2024-03-01"),
        (None, "2024-02-29", "2024-01-31", "2024-03-01"),
        ("now+1d-1d", None, "2026-10-26", "2026-10-27"),
    ],
)
def test_calendar_date_math_resolves_to_one_canonical_window(lower, upper, start, end):
    window = parse_report_window(lower, upper, NOW)
    assert window.start == date.fromisoformat(start)
    assert window.end == date.fromisoformat(end)
    assert window.key == f"{start}:{end}"
    assert window.days == (window.end - window.start).days


@pytest.mark.parametrize(
    "lower,upper",
    [
        ("", None),
        (None, ""),
        ("P30D", None),
        ("lastyear", None),
        ("now-1h", None),
        ("now-2d/invalid", None),
        ("now--1d", None),
        ("now-999999999y", None),
        ("now-3660d", None),
        ("now", "now-1d"),
        ("now", "now+1d"),
        ("2024-01-01", "2023-01-01"),
        ("2026-13-01", None),
    ],
)
def test_invalid_or_unbounded_date_math_fails_before_querying(lower, upper):
    with pytest.raises(ValueError, match="ISO dates"):
        parse_report_window(lower, upper, NOW)


def test_calendar_month_and_year_offsets_clamp_month_end_and_leap_day():
    march = datetime(2026, 3, 31, tzinfo=UTC)
    assert parse_report_window("now-1M", "now", march).start == date(2026, 2, 28)
    leap = datetime(2024, 2, 29, tzinfo=UTC)
    assert parse_report_window("now-1y", "now", leap).start == date(2023, 2, 28)


@pytest.mark.parametrize("start,end", [(None, date(2026, 1, 1)), (date(2026, 1, 1), None)])
def test_half_specified_windows_are_rejected(start, end):
    with pytest.raises(ValueError, match="Both report dates"):
        ReportWindow(start, end)


def test_current_operational_window_has_no_historical_identity():
    window = ReportWindow(None, None)
    assert window.key == "current" and window.days == 1 and window.inclusive_end is None
    with pytest.raises(ValueError, match="explicit calendar"):
        Reports(Client(), window, NOW, capture_started_at=CAPTURED)


@pytest.mark.parametrize(
    "lower,upper,maximum,grain",
    [
        ("2026-08-25", "2026-08-31", 168, "hour"),
        ("now-89d", "now", 90, "day"),
        ("2024-01-01", "2024-12-31", 54, "week"),
        ("now-2y", "now", 25, "month"),
        ("now-3659d", "now", 122, "month"),
    ],
)
@run_async
async def test_closed_and_year_windows_query_exact_bounds_with_bounded_chart_grain(
    lower, upper, maximum, grain
):
    window = parse_report_window(lower, upper, NOW)
    client = Client()
    report = Reports(client, window, NOW, capture_started_at=NOW - timedelta(days=4000))
    result = await report.section("inference")
    assert result.start == window.start and result.end == window.inclusive_end
    assert result.granularity == grain and len(result.data.activity) <= maximum
    assert report.buckets()[-1] < datetime.combine(window.end, datetime.min.time(), tzinfo=UTC)
    for sql, params in client.queries:
        assert params["start"].date() == window.start
        if "overview:inference-ready" in sql:
            continue
        assert params["end"] == min(
            datetime.combine(window.end, datetime.min.time(), tzinfo=UTC),
            NOW.replace(minute=0) + timedelta(hours=1),
        )
        assert "started_at >= {start:" in sql and "started_at < {end:" in sql
        assert "BETWEEN" not in sql


@pytest.mark.parametrize(
    "year,week", [(2026, "2026-10-19T00:00:00Z"), (2025, "2026-10-01T00:00:00Z")]
)
@run_async
async def test_period_archive_replaces_each_days_native_count_without_summing_distinct_estimates(
    year, week
):
    window = ReportWindow(date(year, 1, 1), date(2026, 10, 27))
    archived = [
        {"day": "2026-10-20", "published": 10, "opens": 20, "readers": 12, "accounts_created": 2}
    ]
    client = Client(
        {
            "publishing": [
                {
                    "chart_bucket": week,
                    "total": 0,
                    "published": 5,
                    "automated": 5,
                    "percentiles": [10, 20],
                },
                {"total": 1, "published": 5, "automated": 5, "percentiles": [10, 20]},
            ],
            "audience": [
                {"chart_bucket": week, "opens": 8, "estimated_readers": 4, "new_accounts": 1}
            ],
            "archive": archived,
            "publication-days": [{"day": "2026-10-20", "published": 3}],
            "audience-days": [{"day": "2026-10-20", "opens": 5, "new_accounts": 1}],
        }
    )
    report = Reports(client, window, NOW, capture_started_at=NOW)
    publishing, audience = await asyncio.gather(report.publishing(), report.audience())
    point = next(
        point for point in publishing.activity if point.bucket == week.replace("Z", "+00:00")
    )
    readers = next(
        point for point in audience.activity if point.bucket == week.replace("Z", "+00:00")
    )
    assert point.published == publishing.summary.published == 12
    assert point.automated is publishing.summary.automated is None
    assert point.p50_seconds is publishing.summary.p95_seconds is None
    assert readers.opens == 23 and readers.new_accounts == 2
    assert readers.estimated_readers is None


@pytest.mark.parametrize(
    "value", ["2024-01-01", "2024-01-01T00:00:00", datetime(2024, 1, 1), "2024-01-01T00:00:00Z"]
)
def test_clickhouse_calendar_bucket_keys_are_utc_even_when_output_is_date_only(value):
    assert Reports.key(value) == "2024-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    "chart,fields",
    [
        ("attention", {"full_automation", "reviews", "source_failures", "blockers"}),
        ("publication-rate", {"summary", "activity"}),
        ("publication-latency", {"summary", "activity"}),
        ("audience-activity", {"activity"}),
        ("personalization", {"personalization"}),
        ("queue-pressure", {"queues"}),
        ("throughput", {"throughput"}),
        ("job-outcomes", {"outcomes"}),
        ("ai-activity", {"summary", "activity"}),
        ("ai-breakdown", {"tasks", "models", "efforts"}),
        ("source-output", {"sources", "source_publications_total"}),
        ("topic-coverage", {"topics"}),
    ],
)
@run_async
async def test_each_chart_serializes_only_its_own_typed_fields_and_preserves_report_metadata(
    chart, fields
):
    from devfeed_admin_api.overview_charts import CHART_SECTIONS, OverviewChartReport, chart_report
    from devfeed_admin_api.overview_sections import OperationsSection, OverviewSectionReport
    from devfeed_core.analytics_observation import AnalyticsSnapshot

    group = CHART_SECTIONS[chart]
    saved = (
        OverviewSectionReport(generated_at=NOW, days=1, data=OperationsSection(source_failures=17))
        if group == "operations"
        else await reports().section(group)
    )
    saved.snapshot = AnalyticsSnapshot(
        status="stale", generation="retained-generation", lag_seconds=81
    )
    result = chart_report(chart, saved)
    payload = result.model_dump(mode="json")
    assert payload["chart"] == chart and set(payload["data"]) == fields
    assert payload["days"] == saved.days
    assert payload["start"] == (saved.start.isoformat() if saved.start else None)
    assert payload["end"] == (saved.end.isoformat() if saved.end else None)
    assert payload["snapshot"]["generation"] == "retained-generation"
    assert payload["snapshot"]["status"] == "stale" and payload["snapshot"]["lag_seconds"] == 81
    assert "section" not in payload["data"]
    assert OverviewChartReport.model_validate_json(result.model_dump_json()) == result
    if chart == "attention":
        assert payload["data"]["source_failures"] == 17
    if chart == "publication-rate":
        assert set(payload["data"]["summary"]) == {"published", "automatic_percent"}
        assert set(payload["data"]["activity"][0]) == {"bucket", "published", "automated"}
    if chart == "publication-latency":
        assert set(payload["data"]["summary"]) == {"p50_seconds", "p95_seconds"}
        assert set(payload["data"]["activity"][0]) == {"bucket", "p50_seconds", "p95_seconds"}
