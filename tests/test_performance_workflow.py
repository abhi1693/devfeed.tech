"""Execute the paired workload step to protect order and failure preservation."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    "repeat,failed_root,order",
    [
        (0, "", ["baseline", "candidate"]),
        (1, "", ["candidate", "baseline"]),
        (0, "baseline", ["baseline", "candidate"]),
        (1, "candidate", ["candidate", "baseline"]),
    ],
)
def test_paired_workloads_alternate_order_and_keep_both_results(
    tmp_path, repeat, failed_root, order
):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/performance.yml").read_text())
    script = next(
        step["run"]
        for step in workflow["jobs"]["measure"]["steps"]
        if step.get("name") == "Run isolated base and head samples on the same runner"
    )
    runner = tmp_path / "candidate/.venv/bin/python"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        f"#!{sys.executable}\n"
        + r"""
import os,sys
from pathlib import Path
app_root = sys.argv[sys.argv.index('--app-root') + 1]
with open('order.log', 'a') as log: log.write(app_root + '\n')
raise SystemExit(int(app_root == os.environ['FAILED_ROOT']))
"""
    )
    runner.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "REPEAT": str(repeat), "FAILED_ROOT": failed_root},
        capture_output=True,
        text=True,
    )
    assert result.returncode == bool(failed_root), result.stderr
    assert (tmp_path / "order.log").read_text().splitlines() == order
    for revision, app_root in (("base", "baseline"), ("head", "candidate")):
        code = tmp_path / f"reports/performance/{revision}-{repeat}/exit-code.txt"
        assert int(code.read_text()) == int(app_root == failed_root)
