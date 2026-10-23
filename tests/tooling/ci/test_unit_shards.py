"""Run a real partition to ensure scaling preserves all tests and the merged coverage floor."""

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from coverage import CoverageData

from scripts.ci.coverage_report import summarize
from scripts.ci.unit_shards import merge
from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT


@pytest.fixture(scope="module")
def measured_partition(tmp_path_factory):
    root = tmp_path_factory.mktemp("unit-partition")
    (root / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["apps/api"]\n'
        "[tool.coverage.run]\nbranch = true\nrelative_files = true\n"
        'source = ["apps/api/src"]\n'
        "[tool.coverage.report]\nfail_under = 98\nprecision = 2\n"
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        'pythonpath = ["apps/api/src"]\nmarkers = ["integration"]\n'
    )
    source = root / "apps/api/src/subject.py"
    source.parent.mkdir(parents=True)
    source.write_text("def classify(value):\n    if value:\n        return 1\n    return 0\n")
    (root / "tests").mkdir()
    for name, values, expected in [("true", "[True, 1]", 1), ("false", "[False, 0]", 0)]:
        (root / "tests" / f"test_{name}.py").write_text(
            "import pytest\nfrom subject import classify\n"
            f"@pytest.mark.parametrize('value', {values})\n"
            f"def test_classification(value):\n    assert classify(value) == {expected}\n"
            "@pytest.mark.integration\ndef test_not_unit():\n    assert False\n"
        )
    for index in range(2):
        directory = root / "reports/coverage" / f"python-unit-{index}"
        directory.mkdir(parents=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "-m",
                "pytest",
                "-p",
                "scripts.ci.pytest_shard",
                "-q",
                "-m",
                "not integration",
                "--ci-shard-index",
                str(index),
                "--ci-shard-count",
                "2",
                "--ci-shard-report",
                str(directory / "manifest.json"),
                f"--junitxml={root / 'reports' / f'python-unit-{index}.xml'}",
            ],
            cwd=root,
            env={
                **os.environ,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(ROOT),
                "COVERAGE_FILE": str(directory / "coverage.db"),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return root


@pytest.fixture
def partition(measured_partition, tmp_path, monkeypatch):
    shutil.copytree(measured_partition, tmp_path, dirs_exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_report(root):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "json",
            "--data-file=reports/coverage/python-unit/coverage.db",
            "--include=apps/*/src/**,packages/*/src/**",
            "-o",
            "reports/coverage/unit.json",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_real_unit_shards_partition_cases_and_merge_complementary_coverage(partition):
    manifests = [
        json.loads((partition / f"reports/coverage/python-unit-{index}/manifest.json").read_text())
        for index in range(2)
    ]
    selections = [set(manifest["selected"]) for manifest in manifests]
    assert not selections[0] & selections[1]
    assert len(selections[0] | selections[1]) == 4
    assert all(manifest["collected"] == 4 for manifest in manifests)
    assert all(len({nodeid.split("::")[0] for nodeid in cases}) == 1 for cases in selections)
    for index in range(2):
        data = CoverageData(str(partition / f"reports/coverage/python-unit-{index}/coverage.db"))
        data.read()
        assert data.has_arcs()
        assert data.arcs("apps/api/src/subject.py")
    merge(partition, 2)
    result = write_report(partition)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = summarize(partition, 0)
    assert summary["status"] == "passed"
    assert summary["source_files"] == 1
    assert summary["totals"]["combined_percent"] == 100
    assert summary["tests"] == {"total": 4, "passed": 4, "failed": 0, "skipped": 0}


def test_complete_partition_still_fails_when_merged_coverage_is_below_floor(partition):
    # Both test files may pass without exercising distinct production branches.
    # Preserve their genuine receipts but model this by retaining only one branch's observations.
    shutil.copyfile(
        partition / "reports/coverage/python-unit-0/coverage.db",
        partition / "reports/coverage/python-unit-1/coverage.db",
    )
    merge(partition, 2)
    assert write_report(partition).returncode == 2
    assert summarize(partition, 0)["status"] == "failed"


@pytest.mark.parametrize("change", ["missing", "unexpected", "empty-coverage", "missing-manifest"])
def test_aggregate_rejects_missing_or_extra_shard_artifacts(partition, change):
    directory = partition / "reports/coverage/python-unit-1"
    if change == "missing":
        shutil.rmtree(directory)
    elif change == "unexpected":
        (partition / "reports/coverage/python-unit-2").mkdir()
    elif change == "empty-coverage":
        (directory / "coverage.db").write_bytes(b"")
    else:
        (directory / "manifest.json").unlink()
    with pytest.raises((ValueError, OSError)):
        merge(partition, 2)
    assert not (partition / "reports/python-unit.xml").exists()


@pytest.mark.parametrize("status", ["failure", "error", "skipped"])
def test_passing_manifest_cannot_hide_nonpassing_junit_cases(partition, status):
    path = partition / "reports/python-unit-1.xml"
    tree = ET.parse(path)
    ET.SubElement(next(tree.getroot().iter("testcase")), status)
    tree.write(path)
    with pytest.raises(ValueError, match="failed, errored, or skipped"):
        merge(partition, 2)


@pytest.mark.parametrize("measurement", ["invalid", "no-data", "empty-arcs", "lines-only"])
def test_every_shard_requires_a_valid_nonempty_branch_measurement(partition, measurement):
    path = partition / "reports/coverage/python-unit-1/coverage.db"
    path.unlink()
    if measurement == "invalid":
        path.write_text("not a coverage database")
    else:
        data = CoverageData(str(path))
        if measurement == "lines-only":
            data.add_lines({"apps/api/src/subject.py": [1, 2, 3, 4]})
        else:
            data.add_arcs({} if measurement == "no-data" else {"apps/api/src/subject.py": []})
        data.write()
    with pytest.raises(ValueError, match="invalid coverage database|no measured branch coverage"):
        merge(partition, 2)
    assert not (partition / "reports/python-unit.xml").exists()


@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "interrupted",
        "wrong-index",
        "wrong-count",
        "wrong-attempt",
        "different-suite",
        "empty-selection",
        "unexecuted",
        "overlap",
        "incomplete-union",
    ],
)
def test_aggregate_rejects_failed_stale_or_incomplete_execution(partition, change):
    path = partition / "reports/coverage/python-unit-1/manifest.json"
    document = json.loads(path.read_text())
    if change in {"failed", "interrupted"}:
        document["exit_code"] = 1 if change == "failed" else 2
    elif change == "wrong-index":
        document["index"] = 0
    elif change == "wrong-count":
        document["count"] = 3
    elif change == "wrong-attempt":
        document["run"]["GITHUB_RUN_ATTEMPT"] = "different"
    elif change == "different-suite":
        document["fingerprint"] = "different"
    elif change == "empty-selection":
        document["selected"] = []
    elif change == "unexecuted":
        document["selected"].append("tests/test_extra.py::test_never_ran")
    elif change == "overlap":
        document["selected"] = json.loads(
            (partition / "reports/coverage/python-unit-0/manifest.json").read_text()
        )["selected"]
    else:
        document["selected"][0] = "tests/test_extra.py::test_different_case"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        merge(partition, 2)
    assert not (partition / "reports/python-unit.xml").exists()


@pytest.mark.parametrize("count", [0, -1, True])
def test_aggregate_requires_a_positive_integer_shard_count(partition, count):
    with pytest.raises(ValueError, match="must be positive"):
        merge(partition, count)


def test_plugin_records_a_real_failing_run_instead_of_only_successful_collection(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    path = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.ci.pytest_shard",
            "--ci-shard-report",
            str(path),
            "-q",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(ROOT),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    manifest = json.loads(path.read_text())
    assert manifest["exit_code"] == 1
    assert manifest["collected"] == 1
    assert manifest["selected"] == ["test_failure.py::test_failure"]
