"""Clock-driven policy and control contract regression tests."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.config import Settings, get_settings
from devfeed_core.models import Source
from devfeed_core.polling import decide, observe, resolve_interval, schedule
from devfeed_core.schemas import SourcePatch
from pydantic import ValidationError

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def history(period, days=30):
    state = decide({}, now=NOW, arrivals=1000)
    elapsed = 0
    observed = 0
    intervals = []
    while elapsed < days * 86400:
        elapsed += state["interval"]
        published = int(elapsed // period) if period else 0
        state = decide(state, now=NOW + timedelta(seconds=elapsed), arrivals=published - observed)
        observed = published
        intervals.append(state["interval"])
    return state, intervals


@pytest.mark.parametrize("period", [60, 3600, 7 * 86400, None])
def test_histories_remain_bounded_and_converge(period):
    state, intervals = history(period)
    assert all(300 <= interval <= 86400 for interval in intervals)
    if period and period <= 3600:
        assert state["interval"] < 43200
    else:
        assert state["interval"] > 43200


def test_baseline_stale_burst_and_quiet_to_active():
    baseline = decide({}, now=NOW, arrivals=10000)
    assert baseline["reason"] == "baseline" and baseline["rate"] == 0
    stale = decide(baseline, now=NOW + timedelta(days=8), arrivals=10000)
    assert stale["reason"] == "stale_probe" and stale["interval"] == 3600
    state, _ = history(None)
    now = datetime.fromisoformat(state["observed_at"])
    for _ in range(12):
        delta = state["interval"]
        now += timedelta(seconds=delta)
        previous = delta
        state = decide(state, now=now, arrivals=max(1, delta // 3600))
        assert state["interval"] >= previous / 2
    assert state["interval"] < 7200
    burst = decide(baseline, now=NOW + timedelta(hours=1), arrivals=100000)
    capped = decide(baseline, now=NOW + timedelta(hours=1), arrivals=8)
    assert burst == capped


def test_quiet_growth_depends_on_time_not_number_of_fetches():
    baseline = decide({}, now=NOW, arrivals=0)
    once = decide(baseline, now=NOW + timedelta(hours=12), arrivals=0)
    many = baseline
    for minute in range(5, 721, 5):
        many = decide(many, now=NOW + timedelta(minutes=minute), arrivals=0)
    assert abs(once["interval"] - many["interval"]) < 60
    assert many["quiet_seconds"] == once["quiet_seconds"] == 43200
    assert decide(many, now=NOW, arrivals=10) == many


def record():
    return Source(
        id=uuid.uuid4(), polling_mode="adaptive", polling_state={}, poll_interval_seconds=43200
    )


@pytest.mark.parametrize("gate", [False, True])
@pytest.mark.parametrize("mode", ["fixed", "adaptive"])
def test_effective_gate_keeps_configured_interval(monkeypatch, gate, mode):
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", gate)
    source = record()
    source.polling_mode = mode
    observe(source, NOW, 100, automatic=True)
    assert source.poll_interval_seconds == 43200
    assert resolve_interval(source) == (3600 if gate and mode == "adaptive" else 43200)
    assert source.effective_polling_mode == ("adaptive" if gate and mode == "adaptive" else "fixed")


def test_manual_observations_accumulate_without_changing_clock(monkeypatch):
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", True)
    source = record()
    observe(source, NOW, 100, automatic=True)
    original = dict(source.polling_state)
    for minute in range(1, 20):
        observe(source, NOW + timedelta(minutes=minute), 1, automatic=False)
    assert source.polling_state["observed_at"] == original["observed_at"]
    assert source.polling_state["interval"] == original["interval"]
    assert source.polling_state["pending_arrivals"] == 19
    observe(source, NOW + timedelta(hours=1), 1, automatic=True)
    assert "pending_arrivals" not in source.polling_state
    assert source.polling_state["last_activity_at"] is not None


@pytest.mark.parametrize("interval", [300, 301, 3600, 86399, 86400])
def test_jitter_never_crosses_bounds(monkeypatch, interval):
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", True)
    source = record()
    source.polling_state = {"interval": interval}
    for identifier in [1, 500, 1000]:
        source.id = uuid.UUID(int=identifier)
        delta = (schedule(source, NOW) - NOW).total_seconds()
        assert 300 <= delta <= 86400
        assert abs(delta - interval) <= max(1, interval * 0.05)


def test_contracts_and_settings_reject_invalid_modes_and_bounds():
    assert SourcePatch(polling_mode="adaptive").polling_mode == "adaptive"
    for mode in [None, "unknown", "ADAPTIVE"]:
        with pytest.raises(ValidationError):
            SourcePatch(polling_mode=mode)
    settings = dict(
        database_url="postgresql://example", redis_url="redis://example", _env_file=None
    )
    assert Settings(**settings).adaptive_source_polling_enabled is False
    for minimum, maximum in [(299, 86400), (300, 604801), (900, 300)]:
        with pytest.raises(ValidationError):
            Settings(
                **settings,
                adaptive_source_polling_min_seconds=minimum,
                adaptive_source_polling_max_seconds=maximum,
            )


def test_workload_compares_latency_requests_queue_and_errors():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/testing/adaptive_polling.py"
    spec = importlib.util.spec_from_file_location("adaptive_workload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixed, adaptive = module.compare(False), module.compare(True)
    assert (
        adaptive["groups"]["hourly"]["mean_discovery_seconds"]
        < fixed["groups"]["hourly"]["mean_discovery_seconds"] / 4
    )
    assert adaptive["groups"]["weekly"]["requests"] < fixed["groups"]["weekly"]["requests"] * 0.75
    assert adaptive["minimum_fetches_per_source"] >= 90
    assert adaptive["max_queue_delay_seconds"] <= fixed["max_queue_delay_seconds"]
    assert abs(adaptive["error_rate"] - fixed["error_rate"]) < 0.001


@pytest.mark.parametrize("seconds", [0, -1])
@pytest.mark.parametrize("arrivals", [0, 8])
def test_nonadvancing_clock_keeps_learning_state_without_dividing(seconds, arrivals):
    state = decide({}, now=NOW, arrivals=0)
    state.update(rate=0.1, quiet_seconds=300, interval=1200)
    observed = dict(state)
    result = decide(state, now=NOW + timedelta(seconds=seconds), arrivals=arrivals)
    assert result == observed
    assert result is not state
    assert state == observed
