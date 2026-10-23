import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from devfeed_core.quota_pacing import paced_snapshot, pause_reason, weekly_window

NOW = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
RESET = NOW + 5 * 86400


def response(used=5, reset=RESET):
    return {
        "rateLimits": {
            "primary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": reset}
        }
    }


def test_daily_budget_divides_remaining_week_and_retains_ceiling():
    first = paced_snapshot(None, response(), now=NOW, reserve=20)
    assert first["ceiling_percent"] == 20  # (95 remaining - 20 reserve) / 5 days
    second = paced_snapshot(first, response(15), now=NOW + 3600, reserve=20)
    assert second["ceiling_percent"] == first["ceiling_percent"]
    third = paced_snapshot(second, response(15), now=NOW + 86400, reserve=20)
    assert third["ceiling_percent"] == pytest.approx(15 + 65 / 4)


def test_late_day_gets_only_its_remaining_fraction_and_reset_reopens_budget():
    first = paced_snapshot(None, response(), now=NOW + 43200, reserve=20)
    assert first["ceiling_percent"] == pytest.approx(5 + 75 / 9)
    reset = paced_snapshot(first, response(0, RESET + 7 * 86400), now=RESET, reserve=20)
    assert reset["ceiling_percent"] == pytest.approx(80 / 7)


def test_no_spending_into_reserve_and_other_clients_usage_closes_admission():
    settings = SimpleNamespace(ai_quota_pacing_enabled=True)
    first = paced_snapshot(None, response(), now=NOW, reserve=20)
    current = paced_snapshot(first, response(21), now=NOW + 60, reserve=20)
    connection = SimpleNamespace(get=lambda _: json.dumps(current))
    assert pause_reason(connection, settings=settings, now=NOW + 60) == "codex_quota_paced"
    assert paced_snapshot(None, response(85), now=NOW, reserve=20)["ceiling_percent"] == 80


def test_missing_expired_and_rollover_data_do_not_grant_admission():
    settings = SimpleNamespace(ai_quota_pacing_enabled=True)
    assert (
        pause_reason(SimpleNamespace(get=lambda _: None), settings=settings, now=NOW)
        == "codex_quota_unavailable"
    )
    snapshot = paced_snapshot(None, response(), now=NOW, reserve=20)
    connection = SimpleNamespace(get=lambda _: json.dumps(snapshot))
    assert pause_reason(connection, settings=settings, now=NOW + 180) is None
    assert pause_reason(connection, settings=settings, now=NOW + 181) == "codex_quota_unavailable"
    snapshot["checked_at"] = NOW + 86400
    assert pause_reason(connection, settings=settings, now=NOW + 86400) == "codex_quota_unavailable"


@pytest.mark.parametrize("used", [None, True, "5", float("nan"), -1, 101])
def test_invalid_provider_values_are_rejected(used):
    assert weekly_window(response(used)) is None


def test_weekly_secondary_selected_without_using_short_window():
    data = response()
    data["rateLimits"]["secondary"] = data["rateLimits"]["primary"]
    data["rateLimits"]["primary"] = {"usedPercent": 20, "windowDurationMins": 300}
    assert weekly_window(data) == (5, RESET)


def test_disabled_pacing_does_not_need_redis():
    assert pause_reason(None, settings=SimpleNamespace(ai_quota_pacing_enabled=False)) is None


def test_relationships_use_shared_quota_instead_of_fixed_call_count(monkeypatch):
    from devfeed_core import quota_pacing, topic_decision_budget

    settings = SimpleNamespace(ai_quota_pacing_enabled=True)
    monkeypatch.setattr(topic_decision_budget, "get_settings", lambda: settings)
    monkeypatch.setattr(quota_pacing, "current_pause_reason", lambda settings: None)
    assert topic_decision_budget.relationship_allowance(None)
    monkeypatch.setattr(quota_pacing, "current_pause_reason", lambda settings: "codex_quota_paced")
    assert not topic_decision_budget.relationship_allowance(None)
