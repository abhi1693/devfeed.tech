"""Analytics identities, metadata, range bounds, and retry clocks require no database."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from devfeed_core import (
    analysis,
    analytics_observation,
    article_analysis_steps,
    managed_images,
    source_relevance,
)
from devfeed_core.analytics_observation import AnalyticsSnapshot, pipeline_policy_version
from devfeed_core.config import get_settings
from pydantic import SecretStr, ValidationError


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (analytics_observation, "PIPELINE_POLICY_VERSION"),
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
            "analytics_backfill_batch_size": 500,
            "analytics_poll_seconds": 20,
        }
    )
    assert pipeline_policy_version(changed) == previous


@pytest.mark.parametrize("status", ["fresh", "stale", "catching_up"])
def test_snapshot_metadata_round_trips_timestamps_and_nonnegative_lag(status):
    generated = datetime(2026, 10, 21, tzinfo=UTC)
    value = AnalyticsSnapshot(
        status=status, generated_at=generated, generation="policy-hash", lag_seconds=0
    )
    assert AnalyticsSnapshot.model_validate_json(value.model_dump_json()) == value
    assert value.generated_at == generated
    assert value.lag_seconds == 0
    assert AnalyticsSnapshot(status=status).generated_at is None


@pytest.mark.parametrize(
    "values",
    [
        {"status": "ready"},
        {"status": "refreshing"},
        {"status": "rebuilding"},
        {"status": None},
        {"status": "fresh", "lag_seconds": -0.1},
    ],
)
def test_snapshot_metadata_rejects_unknown_status_and_negative_lag(values):
    with pytest.raises(ValidationError):
        AnalyticsSnapshot.model_validate(values)
