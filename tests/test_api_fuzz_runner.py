"""Fuzz jobs must use owned services, bounded budgets and fresh, nonempty reports."""

import json
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_ci_gates import load_script

api_fuzz = load_script("api_fuzz")


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(api_fuzz.ROOT / "scripts/ci"))
    import services

    monkeypatch.setattr(api_fuzz, "ROOT", tmp_path)
    monkeypatch.setattr(api_fuzz, "version", lambda package: "4.29.2")
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    lifecycle = []

    @contextmanager
    def owned_services():
        lifecycle.append("created")
        try:
            yield {
                "DEVFEED_DATABASE_URL": "postgresql+psycopg://localhost/owned_test",
                "DEVFEED_REDIS_URL": "redis://localhost/15",
            }
        finally:
            lifecycle.append("removed")

    monkeypatch.setattr(services, "disposable_services", owned_services)
    return tmp_path, summary, lifecycle


@pytest.mark.parametrize("profile,budget", [("pr", 600), ("nightly", 1800)])
@pytest.mark.parametrize("explicit_seed", [None, 0, 42, 2**32 - 1])
def test_runner_uses_owned_services_and_records_a_replayable_seed(
    runner, monkeypatch, profile, budget, explicit_seed
):
    root, summary, lifecycle = runner
    monkeypatch.setattr(api_fuzz.secrets, "randbits", lambda bits: 12345)
    arguments = ["api_fuzz.py", "--profile", profile]
    if explicit_seed is not None:
        arguments += ["--seed", str(explicit_seed)]
    monkeypatch.setattr(sys, "argv", arguments)
    seed = explicit_seed if explicit_seed is not None else (20261005 if profile == "pr" else 12345)

    def run(command, **kwargs):
        assert kwargs["timeout"] == budget
        assert kwargs["cwd"] != root
        assert kwargs["env"]["DEVFEED_TEST_DATABASE_URL"].endswith("/owned_test")
        assert kwargs["env"]["DEVFEED_TEST_REDIS_URL"].endswith("/15")
        assert kwargs["env"]["DEVFEED_FUZZ_PROFILE"] == profile
        assert f"--hypothesis-seed={seed}" in command
        assert "--hypothesis-show-statistics" in command
        assert str(root / "tests/test_api_security_boundaries.py") in command
        reports = root / "reports/api-fuzz" / profile
        assert kwargs["env"]["DEVFEED_FUZZ_REPORTS"] == str(reports)
        (reports / "junit.xml").write_text('<testsuite><testcase name="fuzz"/></testsuite>')
        kwargs["stdout"].write("Reproduction and statistics\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(api_fuzz.subprocess, "run", run)
    assert api_fuzz.main() == 0
    assert lifecycle == ["created", "removed"]
    reports = root / "reports/api-fuzz" / profile
    assert json.loads((reports / "run.json").read_text()) == {
        "profile": profile,
        "seed": seed,
        "schemathesis": "4.29.2",
    }
    assert "Reproduction" in (reports / "pytest.log").read_text()
    assert "passed" in summary.read_text()


@pytest.mark.parametrize("seed", ["-1", str(2**32), "42 --override-ini=x", "$(touch injected)"])
def test_invalid_seed_cannot_reach_resources_or_command_execution(runner, monkeypatch, seed):
    root, _summary, lifecycle = runner
    monkeypatch.setattr(sys, "argv", ["api_fuzz.py", "--seed", seed])

    def execute(*args, **kwargs):
        pytest.fail("Invalid seed reached subprocess execution")

    monkeypatch.setattr(api_fuzz.subprocess, "run", execute)
    with pytest.raises(SystemExit) as error:
        api_fuzz.main()
    assert error.value.code == 2
    assert lifecycle == []
    assert not (root / "reports").exists()


@pytest.mark.parametrize("outcome", ["missing", "empty", "skipped", "failure", "timeout"])
def test_runner_fails_closed_and_cleans_up(runner, monkeypatch, outcome):
    root, summary, lifecycle = runner
    monkeypatch.setattr(sys, "argv", ["api_fuzz.py"])
    reports = root / "reports/api-fuzz/pr"
    reports.mkdir(parents=True)
    junit = reports / "junit.xml"
    junit.write_text('<testsuite><testcase name="stale-success"/></testsuite>')

    def run(command, **kwargs):
        assert not junit.exists()
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome in {"empty", "skipped"}:
            junit.write_text(
                "<testsuite/>"
                if outcome == "empty"
                else '<testsuite><testcase name="fuzz"><skipped/></testcase></testsuite>'
            )
        return SimpleNamespace(returncode=2 if outcome == "failure" else 0)

    monkeypatch.setattr(api_fuzz.subprocess, "run", run)
    assert api_fuzz.main() == {"failure": 2, "timeout": 124}.get(outcome, 1)
    assert lifecycle == ["created", "removed"]
    assert "failed" in summary.read_text()
