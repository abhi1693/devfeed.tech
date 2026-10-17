"""Compose fixtures must not inherit an operator's private configuration."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "check_compose_test", Path(__file__).resolve().parents[1] / "scripts/ci/check_compose.py"
)
check_compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_compose)


@pytest.mark.parametrize(
    "configuration",
    [
        {"DEVFEED_YOUTUBE_API_KEY": "inherited-test-youtube-secret"},
        {
            "DEVFEED_X_PIXEL_ENABLED": "true",
            "X_PIXEL_TOKEN": "inherited-test-secret",
            "X_SIGNUP_EVENT_ID": "tw-pc5f8-inherited",
        },
        {
            "DEVFEED_PARTNER_CALLER_KEY": "inherited-test-caller-secret",
            "DEVFEED_PARTNER_CALLER_IP_HEADER": "cf-connecting-ip",
            "DEVFEED_PARTNER_TRACKING_KEY": "inherited-test-tracking-secret",
        },
    ],
    ids=["youtube", "x-pixel", "partner-placement"],
)
def test_compose_render_isolates_inherited_private_configuration(monkeypatch, configuration):
    for key, value in configuration.items():
        monkeypatch.setenv(key, value)
    environments = []

    def run(command, **kwargs):
        environments.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout='{"services": {}}')

    monkeypatch.setattr(check_compose.subprocess, "run", run)
    assert check_compose.render({"POSTGRES_PASSWORD": "fixture-only-password"}) == {"services": {}}
    assert len(environments) == 1
    for key in configuration:
        assert key not in environments[0]
