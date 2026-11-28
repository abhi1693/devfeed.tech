"""Fail-closed coverage inventories and measured counters."""

import importlib.util
import json
import subprocess

import pytest

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT
SPEC = importlib.util.spec_from_file_location(
    "coverage_report", ROOT / "scripts/ci/coverage_report.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


@pytest.fixture
def measurement(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["apps/api", "packages/core"]\n'
        '[tool.coverage.run]\nbranch = true\nsource = ["apps/api/src", "packages/core/src"]\n'
        "[tool.coverage.report]\nfail_under = 65.0\n"
    )
    paths = ["apps/api/src/main.py", "packages/core/src/unimported.py"]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True)
        path.write_text("def value():\n    return 1\n")
    covered = {"covered_lines": 3, "num_statements": 4, "covered_branches": 1, "num_branches": 2}
    document = {
        "meta": {"branch_coverage": True},
        "files": {name: {"summary": dict(covered)} for name in paths},
        "totals": {name: value * 2 for name, value in covered.items()},
    }
    (tmp_path / "reports/coverage").mkdir(parents=True)
    (tmp_path / "reports/python-unit.xml").write_text(
        '<testsuites><testsuite tests="2"><testcase/><testcase/></testsuite></testsuites>'
    )

    def save():
        (tmp_path / "reports/coverage/unit.json").write_text(json.dumps(document))

    save()
    return tmp_path, document, save


def test_report_counts_all_backend_files_and_separates_lines_from_branches(measurement):
    root, _, _ = measurement
    value = report.summarize(root, 0)
    assert value["status"] == "passed" and value["source_files"] == 2
    assert value["totals"]["line_percent"] == 75
    assert value["totals"]["branch_percent"] == 50
    assert value["totals"]["combined_percent"] == pytest.approx(66.6666667)
    assert value["tests"] == {"total": 2, "passed": 2, "failed": 0, "skipped": 0}
    assert set(value["packages"]) == {"apps/api", "packages/core"}
    assert "66.67%" in report.markdown(value)


@pytest.mark.parametrize("exit_code", [1, 2, 5])
def test_successful_testcases_cannot_hide_pytest_or_coverage_failure(measurement, exit_code):
    root, _, _ = measurement
    assert report.summarize(root, exit_code)["status"] == "failed"


@pytest.mark.parametrize("tag", ["failure", "error", "skipped"])
def test_nonpassing_testcases_fail_the_report(measurement, tag):
    root, _, _ = measurement
    (root / "reports/python-unit.xml").write_text(
        f"<testsuite><testcase><{tag}/></testcase></testsuite>"
    )
    assert report.summarize(root, 0)["status"] == "failed"


def test_coverage_below_the_configured_minimum_fails(measurement):
    root, _, _ = measurement
    path = root / "pyproject.toml"
    path.write_text(path.read_text().replace("65.0", "67.0"))
    assert report.summarize(root, 0)["status"] == "failed"


@pytest.mark.parametrize("attempt", ["0", "invalid"])
def test_unit_artifact_metadata_rejects_invalid_run_attempt(measurement, monkeypatch, attempt):
    root, _, _ = measurement
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    with pytest.raises(ValueError):
        report.summarize(root, 0)


@pytest.mark.parametrize(
    "change",
    [
        "missing-file",
        "extra-file",
        "totals",
        "negative",
        "boolean",
        "overcount",
        "branches",
        "empty",
    ],
)
def test_incomplete_or_falsified_measurements_are_rejected(measurement, change):
    root, document, save = measurement
    if change == "missing-file":
        document["files"].pop("packages/core/src/unimported.py")
    elif change == "extra-file":
        document["files"]["tests/test_example.py"] = next(iter(document["files"].values()))
    elif change == "branches":
        document["meta"]["branch_coverage"] = False
    elif change == "empty":
        for entry in document["files"].values():
            entry["summary"] = dict.fromkeys(report.COUNTERS, 0)
        document["totals"] = dict.fromkeys(report.COUNTERS, 0)
    else:
        document["totals"]["covered_lines"] = {
            "totals": 1,
            "negative": -1,
            "boolean": True,
            "overcount": 99,
        }[change]
    save()
    with pytest.raises(ValueError):
        report.summarize(root, 0)


@pytest.mark.parametrize(
    "change", ["source", "duplicate", "branch", "omit", "minimum", "empty-tests"]
)
def test_configuration_cannot_hide_workspaces_or_accept_empty_suites(measurement, change):
    root, _, _ = measurement
    path = root / "pyproject.toml"
    value = path.read_text()
    if change == "source":
        value = value.replace(
            'source = ["apps/api/src", "packages/core/src"]', 'source = ["apps/api/src"]'
        )
    elif change == "duplicate":
        value = value.replace(
            'source = ["apps/api/src", "packages/core/src"]',
            'source = ["apps/api/src", "packages/core/src", "apps/api/src"]',
        )
    elif change == "branch":
        value = value.replace("branch = true", "branch = false")
    elif change == "omit":
        value += 'omit = ["*/low_coverage.py"]\n'
    elif change == "minimum":
        value = value.replace("65.0", "nan")
    else:
        (root / "reports/python-unit.xml").write_text('<testsuite tests="100"/>')
    path.write_text(value)
    with pytest.raises(ValueError):
        report.summarize(root, 0)


@pytest.mark.parametrize(
    "broken", ["missing-coverage", "invalid-json", "missing-junit", "invalid-xml"]
)
def test_cli_always_writes_an_incomplete_report_on_invalid_input(measurement, broken):
    root, _, _ = measurement
    path = root / (
        "reports/coverage/unit.json"
        if broken in {"missing-coverage", "invalid-json"}
        else "reports/python-unit.xml"
    )
    if broken.startswith("missing"):
        path.unlink()
    else:
        path.write_text("invalid input")
    result = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "python",
            str(ROOT / "scripts/ci/coverage_report.py"),
            "--root",
            str(root),
            "--test-exit-code",
            "0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads((root / "reports/coverage/summary.json").read_text()) == {
        "schema": 1,
        "valid": False,
        "status": "incomplete",
    }
    assert "missing or invalid" in (root / "reports/coverage/summary.md").read_text()
