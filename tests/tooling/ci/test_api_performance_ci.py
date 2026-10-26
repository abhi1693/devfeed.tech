"""Local API comparison preserves workload ordering and failed samples."""

import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT


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
        command = [
            sys.executable,
            str(candidate / "scripts/ci/api-performance-pair.py"),
            "--repeat",
            str(repeat),
        ]
        if preserve_only:
            command.append("--preserve-only")
        return subprocess.run(
            command,
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
