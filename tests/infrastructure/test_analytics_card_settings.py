"""Card policy identities track readiness settings without incorporating credentials."""

import pytest
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.config import get_settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ai_enabled", True),
        ("full_automation", True),
        ("image_storage_enabled", True),
        ("image_public_url", "https://images.example"),
        ("analysis_max_candidates", 81),
        ("analysis_fallback_candidates", 9),
        ("ai_content_not_before", "2026-01-01"),
    ],
)
def test_policy_identity_includes_every_readiness_setting(field, value):
    settings = get_settings()
    assert pipeline_policy_version(settings) != pipeline_policy_version(
        settings.model_copy(update={field: value})
    )


def test_policy_identity_excludes_connection_credentials_and_batch_capacity():
    settings = get_settings()
    changed = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://other:private@elsewhere.invalid/unit_test",
            "redis_url": "redis://elsewhere.invalid/15",
            "database_pool_size": 20,
        }
    )
    assert pipeline_policy_version(settings) == pipeline_policy_version(changed)


def test_policy_identity_tracks_logo_contract_changes(monkeypatch):
    from devfeed_core import logos

    before = pipeline_policy_version()
    monkeypatch.setattr(logos, "LOGO_VERSION", "profile-next-logo-version")
    assert pipeline_policy_version() != before
