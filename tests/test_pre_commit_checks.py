"""Exercise hook routing, deletion handling and failure/service isolation."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "pre_commit_checks", Path(__file__).resolve().parents[1] / "scripts/pre_commit_checks.py"
)
assert SPEC and SPEC.loader
hooks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hooks)


def test_docs_do_not_run_project_suites():
    assert hooks.checks_for({"README.md", "docs/development.md"}) == []


@pytest.mark.parametrize("workspace", ["admin", "web"])
def test_frontend_changes_only_test_affected_app(workspace):
    commands = hooks.checks_for({f"apps/{workspace}/src/app/page.tsx"})
    assert ("npm", "run", f"{workspace}:test") in commands
    other = "web" if workspace == "admin" else "admin"
    assert ("npm", "run", f"{other}:test") not in commands
    assert not any("pytest" in command for command in commands)


@pytest.mark.parametrize(
    "path",
    ["packages/ui/src/button.tsx", "packages/theme/tokens.css", "package-lock.json"],
)
def test_shared_frontend_changes_test_both_apps(path):
    commands = hooks.checks_for({path})
    for workspace in ("admin", "web"):
        assert ("npm", "run", f"{workspace}:test") in commands


@pytest.mark.parametrize("path", ["apps/api/src/main.py", "uv.lock", "apps/api/pyproject.toml"])
def test_python_changes_run_types_and_only_unit_tests(path):
    commands = hooks.checks_for({path})
    assert ("uv", "run", "--locked", "mypy") in commands
    assert ("uv", "run", "--locked", "pytest", "-q", "-m", "not integration") in commands
    assert not any(command[0] == "npm" for command in commands)


def test_hook_config_exercises_every_suite_once():
    commands = hooks.checks_for({".pre-commit-config.yaml", "scripts/pre_commit_checks.py"})
    assert len(commands) == len(set(commands))
    for workspace in ("admin", "web"):
        assert ("npm", "run", f"{workspace}:test") in commands
    assert any("pytest" in command for command in commands)
    assert ("node", "--test", "tests/codex_transport.test.cjs") in commands


def test_codex_changes_run_transport_tests():
    assert hooks.checks_for({"infra/codex/transport.cjs"}) == [
        ("node", "--test", "tests/codex_transport.test.cjs")
    ]


def test_staged_deletion_triggers_checks_and_first_failure_stops(monkeypatch):
    monkeypatch.setattr(hooks.subprocess, "check_output", lambda *a, **k: b"apps/web/deleted.ts\0")
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(hooks.subprocess, "run", fail)
    assert hooks.main(["README.md"]) == 17
    assert calls == [("npm", "run", "format:typescript:check")]


def test_runner_removes_integration_credentials(monkeypatch):
    monkeypatch.setenv("DEVFEED_DATABASE_URL", "production")
    monkeypatch.setenv("DEVFEED_REDIS_URL", "production")
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", "production")
    monkeypatch.setenv("DEVFEED_TEST_REDIS_URL", "production")
    monkeypatch.setattr(hooks.subprocess, "check_output", lambda *a, **k: b"")
    environments = []

    def succeed(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(hooks.subprocess, "run", succeed)
    assert hooks.main(["pyproject.toml"]) == 0
    assert environments
    for env in environments:
        assert "database.invalid" in env["DEVFEED_DATABASE_URL"]
        assert "redis.invalid" in env["DEVFEED_REDIS_URL"]
        assert "DEVFEED_TEST_DATABASE_URL" not in env
        assert "DEVFEED_TEST_REDIS_URL" not in env
