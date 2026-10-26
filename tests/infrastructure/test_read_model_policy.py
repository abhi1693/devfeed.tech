"""Read-model identities, metadata, range bounds, and retry clocks require no database."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api import overview_projection
from devfeed_admin_api.overview_windows import MAX_REPORT_DAYS, ReportWindow, current_window
from devfeed_admin_api.pipeline_projection import _next_transition
from devfeed_core import (
    analysis,
    article_analysis_steps,
    managed_images,
    read_models,
    source_relevance,
)
from devfeed_core.config import get_settings
from devfeed_core.read_models import ReadModelSnapshot, pipeline_policy_version
from pydantic import SecretStr, ValidationError


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (read_models, "PIPELINE_POLICY_VERSION"),
        (analysis, "PROMPT_VERSION"),
        (managed_images, "IMAGE_VERSION"),
        (managed_images, "THUMBNAIL_VERSION"),
        (source_relevance, "VERSION"),
    ],
)
def test_policy_identity_changes_with_derivation_contracts(monkeypatch, module, name):
    previous = pipeline_policy_version()
    monkeypatch.setattr(module, name, "next-test-contract")
    assert pipeline_policy_version() != previous


@pytest.mark.parametrize("step", sorted(article_analysis_steps.STEP_SPECS))
def test_every_specialist_prompt_revision_invalidates_pipeline_cards(monkeypatch, step):
    previous = pipeline_policy_version()
    spec = article_analysis_steps.STEP_SPECS[step]
    monkeypatch.setitem(
        article_analysis_steps.STEP_SPECS,
        step,
        replace(spec, prompt_version=spec.prompt_version + "-next"),
    )
    assert pipeline_policy_version() != previous


def test_policy_identity_ignores_mapping_order_release_identity_and_worker_credentials(monkeypatch):
    from devfeed_core import version

    settings = get_settings()
    previous = pipeline_policy_version(settings)
    monkeypatch.setattr(version, "__version__", "999.0.0")
    monkeypatch.setattr(
        article_analysis_steps,
        "STEP_SPECS",
        dict(reversed(list(article_analysis_steps.STEP_SPECS.items()))),
    )
    changed = settings.model_copy(
        update={
            "codex_auth_token": SecretStr("unit-test-only-token"),
            "database_url": "postgresql+psycopg://worker:fixture@database.invalid/unit_test",
            "read_model_batch_size": 500,
            "read_model_poll_seconds": 20,
        }
    )
    assert pipeline_policy_version(changed) == previous


@pytest.mark.parametrize("status", ["fresh", "stale", "refreshing", "rebuilding"])
def test_snapshot_metadata_round_trips_timestamps_and_nonnegative_lag(status):
    generated = datetime(2026, 10, 21, tzinfo=UTC)
    value = ReadModelSnapshot(
        status=status, generated_at=generated, generation="policy-hash", lag_seconds=0
    )
    assert ReadModelSnapshot.model_validate_json(value.model_dump_json()) == value
    assert value.generated_at == generated
    assert value.lag_seconds == 0
    assert ReadModelSnapshot(status=status).generated_at is None


@pytest.mark.parametrize(
    "values", [{"status": "ready"}, {"status": None}, {"status": "fresh", "lag_seconds": -0.1}]
)
def test_snapshot_metadata_rejects_unknown_status_and_negative_lag(values):
    with pytest.raises(ValidationError):
        ReadModelSnapshot.model_validate(values)


@pytest.mark.parametrize("group", sorted(overview_projection.REPORT_GROUPS))
def test_report_window_keys_preserve_every_supported_span_and_exact_calendar_dates(group):
    end = date(2026, 10, 27)
    windows = [
        ReportWindow(end - timedelta(days=days), end) for days in range(1, MAX_REPORT_DAYS + 1)
    ]
    keys = {overview_projection.report_key(group, window) for window in windows}
    independent = group in overview_projection.INDEPENDENT_GROUPS
    assert keys == (
        {(group, "current")} if independent else {(group, window.key) for window in windows}
    )
    assert len(keys) == (1 if independent else 3660)
    earlier = ReportWindow(date(2024, 11, 1), date(2024, 11, 17))
    later = ReportWindow(date(2025, 11, 1), date(2025, 11, 17))
    assert earlier.days == later.days == 16
    assert (
        overview_projection.report_key(group, earlier)
        == overview_projection.report_key(group, later)
    ) is independent
    if independent:
        assert overview_projection.report_key(group, current_window()) == (group, "current")


@pytest.mark.parametrize(
    ("group", "days"), [("unknown", 7), ("publishing", 0), ("publishing", 3661)]
)
def test_report_window_identity_rejects_unknown_groups_and_unbounded_calendar_spans(group, days):
    end = date(2026, 10, 27)
    with pytest.raises(ValueError, match="Unknown report group|between 1 and 3660"):
        window = ReportWindow(end - timedelta(days=days), end)
        overview_projection.report_key(group, window)


def test_report_policy_tracks_its_own_contract_and_bounded_pipeline_identity(monkeypatch):
    monkeypatch.setattr(overview_projection, "pipeline_policy_version", lambda: "a" * 100)
    original = overview_projection.report_policy_version()
    assert original == overview_projection.POLICY_VERSION + ":clickhouse-v2:" + "a" * 64
    monkeypatch.setattr(overview_projection, "POLICY_VERSION", "next-report-contract")
    assert overview_projection.report_policy_version() != original
    assert len(overview_projection.report_policy_version()) <= 100


def test_retry_timer_chooses_first_future_queued_job_and_ignores_unrelated_evidence():
    now = datetime(2026, 10, 21, tzinfo=UTC)
    expected = now + timedelta(seconds=30)
    jobs = [
        None,
        SimpleNamespace(text="Private extracted content"),
        SimpleNamespace(status=None),
        SimpleNamespace(status="running", available_at=now + timedelta(seconds=1)),
        SimpleNamespace(status="failed", available_at=now + timedelta(seconds=2)),
        SimpleNamespace(status="queued", available_at=now - timedelta(seconds=1)),
        SimpleNamespace(status="queued", available_at=now),
        SimpleNamespace(status="queued", available_at=now + timedelta(minutes=1)),
        SimpleNamespace(status="queued", available_at=expected),
    ]
    assert _next_transition(iter(jobs), now) == expected
    assert _next_transition(jobs[:7], now) is None
    assert _next_transition([], now) is None
