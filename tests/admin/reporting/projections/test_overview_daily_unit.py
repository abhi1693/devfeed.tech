"""Calendar rollup composition with supplied aggregate rows, not aggregation/SQL proofs."""

from contextlib import contextmanager
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import overview_daily
from devfeed_core import overview_daily as overview
from devfeed_core.models import OverviewDaily

from tests.support.source_readiness import NOW


def midnight(day):
    return datetime.combine(day, time.min, tzinfo=NOW.tzinfo)


def metrics(**changes):
    return {
        "added": 0,
        "published": 0,
        "accounts": 0,
        "opens": None,
        "readers": None,
        "multi_article_readers": None,
        "content_types": {},
        **changes,
    }


@pytest.mark.parametrize("use_facts", [False, True])
def test_daily_projection_distinguishes_expired_history_from_zero_and_composes_all_series(
    use_facts,
):
    today = NOW.date()
    expired, retained = today - timedelta(days=30), today - timedelta(days=29)
    session = Mock()
    session.info = {"reporting_facts": use_facts}
    session.scalar.side_effect = [5, None] if use_facts else []
    # Counts and viewer breadth are genuine already-grouped read results.
    session.execute.side_effect = [
        [(expired, 2), (today, 1)],
        [(retained, 3)],
        [(today, 2)],
        [(expired, 17), (retained, 4), (today, 3)],
        [(expired, 7, 2), (retained, 3, 1), (today, 2, 0)],
        [(retained, "tutorial", 2), (retained, "news", 1)],
        [(retained, 600.0)],
    ]
    result = overview.daily_metrics(session, midnight(expired), NOW)
    assert len(result) == 31
    assert result[expired] == metrics(added=2)
    assert result[retained] == metrics(
        published=3,
        opens=4,
        readers=3,
        multi_article_readers=1,
        content_types={"tutorial": 2, "news": 1},
        median_publication_seconds=600.0,
    )
    assert result[today] == metrics(
        added=1, accounts=2, opens=3, readers=2, multi_article_readers=0
    )
    assert result[today - timedelta(days=1)] == metrics(opens=0, readers=0, multi_article_readers=0)
    if use_facts:
        queries = [str(call.args[0]) for call in session.execute.call_args_list]
        assert "reporting_article_facts" in queries[0]
        assert "reporting_article_facts" in queries[1]
        assert "reporting_article_facts.content_type" in queries[5]
        assert "reporting_article_facts.published_at" in queries[6]
        assert all("FROM articles" not in query for query in queries)


@pytest.mark.parametrize("guard", ["lease-busy", "fresh"])
def test_refresh_skips_work_without_the_lease_or_while_the_latest_rollup_is_fresh(
    monkeypatch, guard
):
    session = Mock()
    session.scalar.side_effect = (
        [False]
        if guard == "lease-busy"
        else [
            True,
            OverviewDaily(day=NOW.date(), metrics=metrics(), updated_at=NOW - timedelta(minutes=4)),
        ]
    )
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    compute = Mock()
    monkeypatch.setattr(overview, "daily_metrics", compute)

    @contextmanager
    def begin():
        yield session

    overview.refresh_overview_daily(SimpleNamespace(begin=begin))
    compute.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "history", ["none", "current-schema", "legacy-schema", "older-than-window"]
)
def test_refresh_bounds_rebuilds_and_preserves_expired_anonymous_aggregates(monkeypatch, history):
    today = NOW.date()
    latest_day = today - timedelta(days=220 if history == "older-than-window" else 2)
    latest_metrics = (
        metrics(opens=7, readers=3, multi_article_readers=1)
        if history != "legacy-schema"
        else {"added": 1, "opens": 7}
    )
    latest = (
        None
        if history == "none"
        else OverviewDaily(
            day=latest_day, metrics=latest_metrics, updated_at=NOW - timedelta(minutes=6)
        )
    )
    session = Mock()
    session.scalar.side_effect = [True, latest]
    new_day = today
    retained = metrics(opens=19, readers=8, multi_article_readers=3)
    session.execute.return_value.all.return_value = []
    # Return the complete inclusive calendar produced by daily_metrics.
    start_day = (
        today - timedelta(days=179)
        if history in {"none", "older-than-window"}
        else latest_day - timedelta(days=29 if history == "legacy-schema" else 1)
    )
    expired_day = start_day if start_day < today - timedelta(days=29) else None
    values = {}
    for offset in range((today - start_day).days + 1):
        day = start_day + timedelta(days=offset)
        values[day] = (
            metrics()
            if day < today - timedelta(days=29)
            else metrics(opens=0, readers=0, multi_article_readers=0)
        )
    values[new_day] = metrics(opens=2, readers=2, multi_article_readers=0)
    if expired_day:
        values[expired_day] = metrics()
        session.execute.return_value.all.return_value = [(expired_day, retained)]
    compute = Mock(return_value=values)
    monkeypatch.setattr(overview, "daily_metrics", compute)
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)

    @contextmanager
    def begin():
        yield session

    overview.refresh_overview_daily(SimpleNamespace(begin=begin))
    compute.assert_called_once_with(session, midnight(start_day), NOW)
    if expired_day:
        assert values[expired_day]["opens"] == 19 and values[expired_day]["readers"] == 8
        assert values[expired_day]["multi_article_readers"] == 3
    assert values[new_day]["opens"] == 2
    parameters = session.execute.call_args.args[0].compile().params
    assert parameters["updated_at_m0"] == NOW
    assert set(parameters[key] for key in parameters if key.startswith("day_m")) == set(values)


@pytest.mark.parametrize("gap", ["none", "missing-day", "legacy-recent", "legacy-expired"])
def test_read_rollups_reuses_closed_history_and_computes_only_the_earliest_gap(monkeypatch, gap):
    start = NOW.date() - timedelta(days=31)
    days = [start + timedelta(days=i) for i in range(31)]
    rows = [
        OverviewDaily(
            day=day,
            metrics=metrics(opens=8, readers=3, multi_article_readers=1),
            updated_at=NOW - timedelta(minutes=10),
        )
        for day in days
    ]
    earliest = NOW.date()
    if gap == "missing-day":
        earliest = start + timedelta(days=4)
        rows = [row for row in rows if row.day != earliest]
    elif gap.startswith("legacy-"):
        legacy = rows[4] if gap == "legacy-recent" else rows[0]
        legacy.metrics = {"added": 2, "opens": 11}
        if gap == "legacy-recent":
            earliest = legacy.day
    session = Mock()
    session.scalars.return_value = rows
    live = {
        day: metrics(opens=1, readers=1, multi_article_readers=0)
        for day in [*days, NOW.date()]
        if day >= earliest
    }
    compute = Mock(return_value=live)
    monkeypatch.setattr(overview, "daily_metrics", compute)
    result = overview.read_daily_metrics(session, midnight(start), NOW)
    compute.assert_called_once_with(session, midnight(earliest), NOW)
    assert len(result) == 32 and result[NOW.date()]["opens"] == 1
    for row in rows:
        assert result[row.day]["opens"] == row.metrics["opens"]
        if "readers" not in row.metrics and row.day < earliest:
            assert result[row.day]["readers"] is None
    if gap == "missing-day":
        assert result[earliest]["opens"] == 1
    if gap == "legacy-recent":
        assert result[earliest]["readers"] == 1
        assert result[earliest]["opens"] == 11


def test_live_daily_measurements_replace_previously_retained_rollup_counts(monkeypatch):
    today = NOW.date()
    yesterday = today - timedelta(days=1)
    old = metrics(opens=90, readers=40, multi_article_readers=20)
    values = {
        yesterday: metrics(opens=8, readers=4, multi_article_readers=2),
        today: metrics(opens=3, readers=2, multi_article_readers=1),
    }
    latest = OverviewDaily(day=today, metrics=old, updated_at=NOW - timedelta(minutes=6))
    session = Mock()
    session.scalar.side_effect = [True, latest]
    session.execute.return_value.all.return_value = [(yesterday, old), (today, old)]
    compute = Mock(return_value=values)
    monkeypatch.setattr(overview_daily, "daily_metrics", compute)
    monkeypatch.setattr(overview_daily, "utcnow", lambda: NOW)

    @contextmanager
    def begin():
        yield session

    overview_daily.refresh_overview_daily(SimpleNamespace(begin=begin))

    compute.assert_called_once_with(session, midnight(yesterday), NOW)
    assert values[yesterday] == metrics(opens=8, readers=4, multi_article_readers=2)
    assert values[today] == metrics(opens=3, readers=2, multi_article_readers=1)
    assert old == metrics(opens=90, readers=40, multi_article_readers=20)
    writes = session.execute.call_args.args[0].compile().params
    assert writes["metrics_m1"] == values[today] and writes["updated_at_m1"] == NOW


def test_fact_corrections_replace_closed_article_history_and_remove_deleted_publication_latency(
    monkeypatch,
):
    first, second = NOW.date() - timedelta(days=2), NOW.date() - timedelta(days=1)
    stored = [
        OverviewDaily(
            day=day,
            metrics=metrics(
                added=99,
                published=88,
                opens=12,
                readers=6,
                multi_article_readers=2,
                content_types={"article": 88},
                median_publication_seconds=900,
            ),
            updated_at=NOW - timedelta(days=1),
        )
        for day in (first, second)
    ]
    session = SimpleNamespace(info={"reporting_facts": True}, scalars=lambda _: stored)
    corrected = {
        first: metrics(added=1, published=0),
        second: metrics(
            added=2, published=1, content_types={"tutorial": 1}, median_publication_seconds=2.5
        ),
        NOW.date(): metrics(added=3, published=0, opens=2, readers=1, multi_article_readers=0),
    }
    compute = Mock(side_effect=[{NOW.date(): corrected[NOW.date()]}, corrected])
    monkeypatch.setattr(overview, "daily_metrics", compute)
    values = overview.read_daily_metrics(session, midnight(first), NOW)
    assert compute.call_args_list[0].args == (session, midnight(NOW.date()), NOW)
    assert compute.call_args_list[1].args == (session, midnight(first), NOW)
    assert values[first]["added"] == 1 and values[first]["published"] == 0
    assert values[first]["content_types"] == {}
    assert "median_publication_seconds" not in values[first]
    assert values[second]["published"] == 1 and values[second]["content_types"] == {"tutorial": 1}
    assert values[second]["median_publication_seconds"] == 2.5
    # Correcting article contributions preserves retained anonymous engagement.
    assert values[first]["opens"] == values[second]["opens"] == 12
    assert values[first]["readers"] == values[second]["readers"] == 6
    assert values[NOW.date()]["opens"] == 2
