"""API performance selection and paired execution must preserve trustworthy reports."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def performance_jobs():
    return yaml.safe_load((ROOT / ".github/workflows/performance.yml").read_text())["jobs"]


def compare_step(name):
    return next(step for step in performance_jobs()["compare"]["steps"] if step.get("name") == name)


def measure_step(name):
    return next(step for step in performance_jobs()["measure"]["steps"] if step.get("name") == name)


@pytest.fixture
def paired_harness(tmp_path):
    candidate = tmp_path / "candidate"
    baseline = tmp_path / "baseline"
    for application in (candidate, baseline):
        python = application / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    scripts = candidate / "scripts"
    scripts.mkdir()
    (scripts / "ci").mkdir()
    shutil.copyfile(
        ROOT / "scripts/ci/api-performance-pair.py", scripts / "ci/api-performance-pair.py"
    )
    (scripts / "load-test.py").write_text(
        textwrap.dedent("""\
        import argparse
        import json
        import os
        import sys
        from pathlib import Path

        parser = argparse.ArgumentParser()
        for name in ("app-root", "app-python", "output"):
            parser.add_argument("--" + name, type=Path, required=True)
        for name in ("users", "spawn-rate", "seconds", "rows"):
            parser.add_argument("--" + name, type=int, required=True)
        args = parser.parse_args()
        side, repeat = args.output.name.split("-")
        with Path(os.environ["PAIR_TEST_TRACE"]).open("a") as trace:
            trace.write(json.dumps({
                "side": side,
                "app_root": str(args.app_root),
                "app_python": str(args.app_python),
                "workload": [args.users, args.spawn_rate, args.seconds, args.rows],
                "initial_codes": {
                    revision: (args.output.parent / f"{revision}-{repeat}" / "exit-code.txt")
                        .read_text().strip()
                    for revision in ("base", "head")
                },
            }) + "\\n")
        print(f"{side} standard output")
        print(f"{side} standard error", file=sys.stderr)
        raise SystemExit(json.loads(os.environ["PAIR_TEST_FAILURES"]).get(side, 0))
        """)
    )
    trace = tmp_path / "trace.jsonl"
    output = tmp_path / "reports/performance"

    def invoke(repeat, *, failures=None, preserve_only=False):
        step = measure_step(
            "Preserve incomplete samples" if preserve_only else "Run one runner pair"
        )
        return subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            cwd=tmp_path,
            env={
                **os.environ,
                "PAIR_REPEAT": str(repeat),
                "PAIR_TEST_TRACE": str(trace),
                "PAIR_TEST_FAILURES": json.dumps(failures or {}),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    return invoke, output, trace


@pytest.mark.parametrize(
    "repeat,order", [(0, ["base", "head"]), (1, ["head", "base"]), (2, ["base", "head"])]
)
def test_paired_samples_alternate_order_without_changing_workload(paired_harness, repeat, order):
    invoke, output, trace = paired_harness
    result = invoke(repeat)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [call["side"] for call in calls] == order
    assert calls[0]["initial_codes"] == {"base": "incomplete", "head": "incomplete"}
    assert calls[1]["initial_codes"] == {order[0]: "0", order[1]: "incomplete"}
    for call in calls:
        application = output.parents[1] / ("baseline" if call["side"] == "base" else "candidate")
        assert call["app_root"] == str(application)
        assert call["app_python"] == str(application / ".venv/bin/python")
        assert call["workload"] == [16, 4, 60, 1000]
        destination = output / f"{call['side']}-{repeat}"
        assert (destination / "exit-code.txt").read_text() == "0\n"
        log = (destination / "runner.log").read_text()
        assert f"{call['side']} standard output" in log
        assert f"{call['side']} standard error" in log


@pytest.mark.parametrize("repeat", [0, 1], ids=["base-first", "head-first"])
@pytest.mark.parametrize(
    "failures", [{"base": 7}, {"head": 9}, {"base": 7, "head": 9}], ids=["base", "head", "both"]
)
def test_failed_samples_still_attempt_both_revisions_and_keep_outcomes(
    paired_harness, repeat, failures
):
    invoke, output, trace = paired_harness
    result = invoke(repeat, failures=failures)
    assert result.returncode == 1
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(calls) == 2 and {call["side"] for call in calls} == {"base", "head"}
    for side in ("base", "head"):
        destination = output / f"{side}-{repeat}"
        assert (destination / "exit-code.txt").read_text() == f"{failures.get(side, 0)}\n"
        assert f"{side} standard output" in (destination / "runner.log").read_text()


def test_sample_launch_errors_are_preserved_for_both_revisions(paired_harness):
    invoke, output, trace = paired_harness
    (output.parents[1] / "candidate/.venv/bin/python").unlink()
    result = invoke(0)
    assert result.returncode == 1
    assert not trace.exists()
    for side in ("base", "head"):
        destination = output / f"{side}-0"
        assert (destination / "exit-code.txt").read_text() == "127\n"
        assert f"Unable to launch {side} sample" in (destination / "runner.log").read_text()


def test_setup_failure_preserves_incomplete_and_completed_sample_markers(paired_harness):
    invoke, output, trace = paired_harness
    destination = output / "base-0"
    destination.mkdir(parents=True)
    (destination / "exit-code.txt").write_text("7\n")
    (destination / "runner.log").write_text("Original failure\n")
    result = invoke(0, preserve_only=True)
    assert result.returncode == 0, result.stderr
    assert not trace.exists()
    assert (destination / "exit-code.txt").read_text() == "7\n"
    assert (destination / "runner.log").read_text() == "Original failure\n"
    assert (output / "head-0/exit-code.txt").read_text() == "incomplete\n"


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
    assert jobs["measure"]["strategy"]["matrix"] == {"repeat": [0, 1, 2]}
    assert jobs["measure"]["strategy"]["fail-fast"] is False
    checkouts = {
        step["with"]["path"]: step
        for step in jobs["measure"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    }
    for path, revision in (("candidate", "head"), ("baseline", "base")):
        assert checkouts[path]["with"]["ref"] == (
            "${{ github.event.pull_request." + revision + ".sha }}"
        )
        assert "if" not in checkouts[path]
    assert "if" not in measure_step("Install the baseline application")
    assert measure_step("Run one runner pair")["env"] == {"PAIR_REPEAT": "${{ matrix.repeat }}"}
    assert measure_step("Preserve incomplete samples")["if"] == "always()"
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
