"""Operational capacity projections over aggregated reads and provider quota receipts.

Repository adapters supply already-grouped results. Assertions cover projection,
validation and admission math, leaving PostgreSQL aggregation to integration tests.
"""

import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import pipeline_capacity, pipeline_metrics, quota_pacing, topic_decision_metrics
from redis.exceptions import ConnectionError as RedisConnectionError
from test_source_readiness_unit import NOW, configuration


@pytest.mark.parametrize("observed", [False, True])
def test_throughput_keeps_all_hour_buckets_and_distinguishes_unknown_capacity_from_zero(
    monkeypatch, observed
):
    now = NOW + timedelta(minutes=30)
    first = now.replace(minute=0) - timedelta(hours=23)
    latest = now.replace(minute=0)
    rows = [
        {"hour": first, "kind": "topic_decisions", "count": 6},
        {"hour": latest, "kind": "articles_published", "count": 4},
        {"hour": latest, "kind": "article_analyses", "count": 9},
    ]
    queued = {
        "kind": "topic",
        "queued": 4,
        "running": 2,
        "oldest_due_seconds": Decimal("900"),
        "completed": 3,
        "failed": 1,
        "median_processing_seconds": Decimal("12.5"),
    }
    session = Mock()
    session.execute.side_effect = [
        SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows)),
        SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [queued])),
    ]
    # One idle and one busy dedicated topic worker, one idle article worker,
    # plus one busy mixed worker eligible for either lane but reserved for neither.
    capacity = (
        {
            "observed": True,
            "cooldown_seconds": 0,
            "topic_workers": 2,
            "article_workers": 1,
            "idle_topic_workers": 1,
            "idle_article_workers": 1,
            "shared_workers": 1,
            "eligible_topic_workers": 3,
            "eligible_article_workers": 2,
            "busy_topic_workers": 2,
            "busy_article_workers": 1,
        }
        if observed
        else {"observed": False}
    )
    monkeypatch.setattr(pipeline_metrics, "observe_capacity", lambda: capacity)
    monkeypatch.setattr(
        pipeline_capacity,
        "get_settings",
        lambda: configuration(topic_decision_max_pending=12, topic_decision_worker_buffer=4),
    )
    result = pipeline_metrics.pipeline_throughput(session, now)
    assert [row.hour for row in result.hours] == [
        first + timedelta(hours=index) for index in range(24)
    ]
    assert result.hours[0].topic_decisions == 6
    assert result.hours[-1].articles_published == 4 and result.hours[-1].article_analyses == 9
    assert all(
        row.topic_decisions == row.articles_published == row.article_analyses == 0
        for row in result.hours[1:-1]
    )
    assert result.topic_decisions_per_hour == round(6 / 23.5, 2)
    assert result.articles_published_per_hour == round(4 / 23.5, 2)
    assert len(result.queues) == 1 and result.queues[0].model_dump() == {
        **queued,
        "oldest_due_seconds": 900.0,
        "median_processing_seconds": 12.5,
    }
    assert result.capacity_observed is observed and result.topic_admission_limit == (
        8 if observed else 4
    )
    if observed:
        for key, value in capacity.items():
            if key != "observed":
                assert getattr(result, key) == value
    else:
        assert result.topic_workers is result.article_workers is result.cooldown_seconds is None
    assert all(
        call.args[1] == {"start": first, "now": now} for call in session.execute.call_args_list
    )
    session.commit.assert_not_called()


@pytest.mark.parametrize("completed", [False, True])
def test_decision_metrics_preserve_integer_usage_and_distinguish_unknown_cost_from_zero(completed):
    counts = {
        "pending": 8,
        "actionable": 3,
        "deferred": 2,
        "awaiting_review": 3,
        "approved": 2 if completed else 0,
        "rejected": 2 if completed else 0,
    }
    usage = {
        "calls": 8 if completed else 0,
        "tokens": Decimal(1000 if completed else 0),
        "charged_tokens": Decimal(800 if completed else 0),
        "unreported_calls": int(completed),
        "repeat_calls": 2 if completed else 0,
        "escalations": 2 if completed else 0,
        "tokens_per_completed_topic": Decimal(200) if completed else None,
    }
    session = Mock()
    session.execute.side_effect = [
        SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: counts)),
        SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: usage)),
        SimpleNamespace(all=lambda: [("budget_limit", 1), ("unspecified", 1)]),
    ]
    start = NOW - timedelta(hours=2)
    result = topic_decision_metrics.decision_metrics(session, start, NOW)
    assert result.pending == result.actionable + result.deferred + result.awaiting_review == 8
    assert result.decisions_per_hour == (2 if completed else 0)
    assert result.estimated_drain_hours == (1.5 if completed else None)
    assert result.escalation_percent == (25 if completed else None)
    assert result.tokens_per_completed_topic == (200 if completed else None)
    assert type(result.tokens) is type(result.charged_tokens) is int
    assert result.tokens == (1000 if completed else 0)
    assert result.deferred_reasons == {"budget_limit": 1, "unspecified": 1}
    assert json.loads(result.model_dump_json()) == result.model_dump()
    assert all(
        call.args[1] == {"start": start, "now": NOW} for call in session.execute.call_args_list[:2]
    )
    session.commit.assert_not_called()


def quota_response():
    return {
        "rateLimits": {
            "primary": {
                "usedPercent": 5,
                "windowDurationMins": 10080,
                "resetsAt": NOW.timestamp() + 5 * 86400,
            }
        }
    }


@pytest.mark.parametrize("failure", ["disabled", "redis", "malformed", "available"])
def test_live_quota_read_releases_its_connection_and_never_admits_unknown_capacity(
    monkeypatch, failure
):
    settings = configuration(ai_quota_pacing_enabled=failure != "disabled")
    monkeypatch.setattr(quota_pacing, "get_settings", lambda: settings)
    monkeypatch.setattr(quota_pacing, "time", SimpleNamespace(time=lambda: NOW.timestamp()))
    snapshot = quota_pacing.paced_snapshot(None, quota_response(), now=NOW.timestamp(), reserve=20)
    connection = Mock()
    connection.get.return_value = (
        b"private-invalid-json" if failure == "malformed" else json.dumps(snapshot).encode()
    )
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    connect = Mock(return_value=context)
    if failure == "redis":
        connect.side_effect = RedisConnectionError("private-redis-token")
    monkeypatch.setattr(quota_pacing, "create_redis", connect)
    result = quota_pacing.current_pause_reason()
    assert result == ("codex_quota_unavailable" if failure in {"redis", "malformed"} else None)
    if failure == "disabled":
        connect.assert_not_called()
    else:
        connect.assert_called_once_with(settings)
        if failure != "redis":
            context.__exit__.assert_called_once()
            connection.get.assert_called_once_with(quota_pacing.quota_key())


@pytest.mark.parametrize(
    "response", [None, {"rateLimits": None}, {"rateLimits": {"primary": None}}]
)
def test_absent_weekly_window_cannot_create_a_pacing_receipt(response):
    assert quota_pacing.weekly_window(response) is None
    assert quota_pacing.paced_snapshot(None, response, now=NOW.timestamp(), reserve=20) is None


def test_past_reset_cannot_extend_an_old_weekly_budget():
    response = quota_response()
    response["rateLimits"]["primary"]["resetsAt"] = NOW.timestamp() - 1
    assert quota_pacing.paced_snapshot(None, response, now=NOW.timestamp(), reserve=20) is None
