"""API performance applicability must fail closed and report unmeasured changes honestly."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def performance_jobs():
    return yaml.safe_load((ROOT / ".github/workflows/performance.yml").read_text())["jobs"]


def compare_step(name):
    return next(step for step in performance_jobs()["compare"]["steps"] if step.get("name") == name)


@pytest.mark.parametrize(
    "result,needed,valid",
    [
        ("success", "true", True),
        ("success", "false", True),
        ("failure", "false", False),
        ("cancelled", "true", False),
        ("skipped", "", False),
        ("success", "", False),
        ("success", "unknown", False),
    ],
)
def test_performance_selection_errors_cannot_pass(tmp_path, result, needed, valid):
    summary = tmp_path / "job-summary.md"
    command = subprocess.run(
        ["bash", "-e", "-c", compare_step("Validate selection")["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "SELECT_RESULT": result,
            "MEASURE_NEEDED": needed,
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (command.returncode == 0) is valid
    if not valid:
        report = (tmp_path / "reports/performance/summary.md").read_text()
        assert "**ERROR**" in report and "selection failed" in report
        assert summary.read_text() == report


def test_unchanged_api_report_does_not_claim_latency_or_improvement(tmp_path):
    (tmp_path / "reports/performance").mkdir(parents=True)
    summary = tmp_path / "job-summary.md"
    subprocess.run(
        ["bash", "-e", "-c", compare_step("Report unchanged API")["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "BASE_SHA": "a" * 40,
            "HEAD_SHA": "b" * 40,
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        check=True,
    )
    report = (tmp_path / "reports/performance/summary.md").read_text()
    assert "**Not measured:** public API inputs are unchanged" in report
    assert "a" * 40 in report and "b" * 40 in report
    assert "p95" not in report and "IMPROVED" not in report
    assert summary.read_text() == report
    assert json.loads((tmp_path / "reports/performance/comparison.json").read_text()) == {
        "verdict": "NOT_MEASURED",
        "reason": "public API inputs unchanged",
    }


def test_selection_controls_samples_without_weakening_the_required_workflow():
    jobs = performance_jobs()
    assert jobs["measure"]["needs"] == "select"
    assert "needs.select.outputs.needed == 'true'" in jobs["measure"]["if"]
    assert jobs["measure"]["strategy"]["matrix"] == {
        "revision": ["base", "head"],
        "repeat": [0, 1, 2],
    }
    assert set(jobs["compare"]["needs"]) == {"select", "measure"}
    assert "always()" in jobs["compare"]["if"]
    assert compare_step("Evaluate regression and noise gates")["if"] == (
        "needs.select.outputs.needed == 'true'"
    )
    assert compare_step("Report unchanged API")["if"] == "needs.select.outputs.needed == 'false'"
    assert any(
        step.get("run") == "node --test scripts/ci/api-performance.test.mjs"
        for step in jobs["select"]["steps"]
    )
    assert (
        'then .performance.result == "success"' in (ROOT / ".github/workflows/ci.yml").read_text()
    )
