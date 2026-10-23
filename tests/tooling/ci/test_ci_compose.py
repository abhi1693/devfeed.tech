"""Compose fixtures must not inherit an operator's private configuration."""

import importlib.util
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "check_compose_test", REPO_ROOT / "scripts/ci/check_compose.py"
)
check_compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_compose)


@pytest.mark.parametrize(
    "configuration",
    [
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
    ids=["x-pixel", "partner-placement"],
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


@pytest.mark.parametrize("build", [False, True], ids=["registry", "local-build"])
def test_private_mcp_routing_is_confined_to_reader(build):
    check_compose.check_private_mcp_routing(
        {"POSTGRES_PASSWORD": "fixture-only-password", "COMPOSE_PROFILES": "mcp"},
        {"DEVFEED_MCP_PUBLIC_URL": "https://agents.example.test/mcp"},
        build=build,
    )
