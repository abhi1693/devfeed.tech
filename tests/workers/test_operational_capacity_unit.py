"""Operational capacity projections over aggregated reads and provider quota receipts.

Repository adapters supply already-grouped results. Assertions cover projection,
validation and admission math, leaving PostgreSQL aggregation to integration tests.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import quota_pacing
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.support.source_readiness import NOW, configuration


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
