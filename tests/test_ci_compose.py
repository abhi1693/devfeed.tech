"""Compose fixtures must not inherit an operator's X API credentials."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "check_compose_test", Path(__file__).resolve().parents[1] / "scripts/ci/check_compose.py"
)
check_compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_compose)


def test_compose_render_isolates_inherited_x_configuration(monkeypatch):
    monkeypatch.setenv("DEVFEED_X_PIXEL_ENABLED", "true")
    monkeypatch.setenv("X_PIXEL_TOKEN", "inherited-test-secret")
    monkeypatch.setenv("X_SIGNUP_EVENT_ID", "tw-pc5f8-inherited")
    environments = []

    def run(command, **kwargs):
        environments.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout='{"services": {}}')

    monkeypatch.setattr(check_compose.subprocess, "run", run)
    assert check_compose.render({"POSTGRES_PASSWORD": "fixture-only-password"}) == {"services": {}}
    assert len(environments) == 1
    for key in ("DEVFEED_X_PIXEL_ENABLED", "X_PIXEL_TOKEN", "X_SIGNUP_EVENT_ID"):
        assert key not in environments[0]
