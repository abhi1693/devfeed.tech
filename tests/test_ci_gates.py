"""Regression checks for the release pipeline's fail-closed report gates."""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reports = load_script("check_reports")
images = load_script("check_image_index")
shards = load_script("pytest_shard")
READER_SUITES = ("web", "chrome", "edge", "partner")
READER_SHA = "a" * 40


def reader_jobs():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]


def reader_step(name):
    return next(
        step for step in reader_jobs()["reader-parity"]["steps"] if step.get("name") == name
    )


@pytest.fixture
def reader_reports(tmp_path):
    for suite in READER_SUITES:
        directory = tmp_path / "reader-reports" / suite / "reports"
        directory.mkdir(parents=True)
        (directory / "reader-parity-result.json").write_text(
            json.dumps({"suite": suite, "sha": READER_SHA, "status": "success"})
        )
        if suite != "partner":
            for state in ("guest", "signed-in"):
                report = directory / "accessibility" / suite / state / "summary.json"
                report.parent.mkdir(parents=True)
                report.write_text(
                    json.dumps(
                        {
                            "status": "passed",
                            "scans": [{"violations": [], "incomplete": 0}],
                            "interactions": [{"status": "passed"}],
                        }
                    )
                )
    return tmp_path


def run_reader_gate(directory, result="success"):
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", reader_step("Require all reader suites")["run"]],
        cwd=directory,
        env={**os.environ, "READER_RESULT": result, "SOURCE_SHA": READER_SHA},
        capture_output=True,
        text=True,
    )


def test_reader_matrix_keeps_all_suites_and_strict_aggregate_and_report_boundaries():
    jobs = reader_jobs()
    worker = jobs["reader-browser"]
    assert worker["strategy"]["fail-fast"] is False
    assert worker["strategy"]["matrix"] == {"suite": list(READER_SUITES)}
    assert worker["runs-on"] == "ubuntu-24.04"
    assert "continue-on-error" not in worker
    check = next(step for step in worker["steps"] if step.get("name") == "Check")
    assert check["env"]["READER_SUITE"] == "${{ matrix.suite }}"
    worker_reports = next(step for step in worker["steps"] if step.get("name") == "Reports")
    assert worker_reports["if"] == "always()"
    assert worker_reports["with"]["if-no-files-found"] == "error"
    assert (
        worker_reports["with"]["name"]
        == "reader-parity-${{ matrix.suite }}-${{ github.run_attempt }}"
    )
    aggregate = jobs["reader-parity"]
    assert aggregate["name"] == "E2E / Reader checks"
    assert aggregate["needs"] == "reader-browser"
    assert aggregate["if"] == "always() && !cancelled()"
    downloads = [
        step for step in aggregate["steps"] if "download-artifact@" in step.get("uses", "")
    ]
    assert len(downloads) == len(READER_SUITES)
    assert {step["with"]["name"]: step["with"]["path"] for step in downloads} == {
        f"reader-parity-{suite}-${{{{ github.run_attempt }}}}": f"reader-reports/{suite}"
        for suite in READER_SUITES
    }
    assert all("continue-on-error" not in step for step in downloads)
    assert reader_step("Require all reader suites")["env"] == {
        "READER_RESULT": "${{ needs.reader-browser.result }}",
        "SOURCE_SHA": "${{ github.sha }}",
    }
    summary = reader_step("Summary")
    assert summary["run"] == "node scripts/ci/accessibility-report.mjs"
    assert summary["if"] == "always() && !cancelled()"
    assert aggregate["steps"].index(reader_step("Require all reader suites")) < aggregate[
        "steps"
    ].index(summary)
    assert reader_step("Reports")["with"]["name"] == "reader-parity-${{ github.run_attempt }}"
    assert reader_step("Reports")["with"]["if-no-files-found"] == "error"
    assert "reader-parity" in jobs["required"]["needs"]
    assert "reader-parity" in jobs["release-images"]["needs"]


@pytest.mark.parametrize("suite", READER_SUITES)
@pytest.mark.parametrize("exit_code", [0, 7])
def test_reader_worker_cannot_write_success_before_its_checks_pass(tmp_path, suite, exit_code):
    script = tmp_path / "scripts/ci/reader-parity.sh"
    script.parent.mkdir(parents=True)
    script.write_text('printf "%s\\n" "$1" > attempted-suite\nexit "$CHECK_EXIT"\n')
    worker = reader_jobs()["reader-browser"]
    check = next(step for step in worker["steps"] if step.get("name") == "Check")
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", check["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "READER_SUITE": suite,
            "GITHUB_SHA": READER_SHA,
            "CHECK_EXIT": str(exit_code),
        },
        capture_output=True,
        text=True,
    )
    assert (tmp_path / "attempted-suite").read_text().strip() == suite
    marker = tmp_path / "reports/reader-parity-result.json"
    if exit_code:
        assert result.returncode != 0
        assert not marker.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(marker.read_text()) == {
            "suite": suite,
            "sha": READER_SHA,
            "status": "success",
        }


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_reader_aggregate_rejects_unsuccessful_matrix_even_with_complete_reports(
    reader_reports, result
):
    assert run_reader_gate(reader_reports, result).returncode != 0


@pytest.mark.parametrize("suite", READER_SUITES)
@pytest.mark.parametrize(
    "defect", ["missing", "invalid-json", "wrong-suite", "stale-sha", "failed"]
)
def test_reader_aggregate_rejects_missing_stale_or_unsuccessful_suite_reports(
    reader_reports, suite, defect
):
    marker = reader_reports / "reader-reports" / suite / "reports/reader-parity-result.json"
    if defect == "missing":
        marker.unlink()
    elif defect == "invalid-json":
        marker.write_text("not-json")
    else:
        data = json.loads(marker.read_text())
        key, value = {
            "wrong-suite": ("suite", "unexpected"),
            "stale-sha": ("sha", "b" * 40),
            "failed": ("status", "failure"),
        }[defect]
        marker.write_text(json.dumps({**data, key: value}))
    result = run_reader_gate(reader_reports)
    assert result.returncode != 0
    assert f"Missing or unsuccessful current-source report: {suite}" in result.stdout


@pytest.mark.parametrize("surface", ["web", "chrome", "edge"])
@pytest.mark.parametrize("state", ["guest", "signed-in"])
def test_reader_aggregate_still_requires_every_runtime_session_summary(
    reader_reports, surface, state
):
    report = (
        reader_reports
        / f"reader-reports/{surface}/reports/accessibility/{surface}/{state}/summary.json"
    )
    report.unlink()
    gate = run_reader_gate(reader_reports)
    assert gate.returncode == 0, gate.stdout + gate.stderr
    summary = subprocess.run(
        ["node", str(ROOT / "scripts/ci/accessibility-report.mjs")],
        cwd=reader_reports,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(reader_reports / "step-summary.md")},
        capture_output=True,
        text=True,
    )
    assert summary.returncode != 0
    assert f"| {surface} | {state} | MISSING |" in summary.stdout


def test_reader_aggregate_accepts_current_successes_and_copies_all_six_summaries(reader_reports):
    result = run_reader_gate(reader_reports)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = subprocess.run(
        ["node", str(ROOT / "scripts/ci/accessibility-report.mjs")],
        cwd=reader_reports,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(reader_reports / "step-summary.md")},
        capture_output=True,
        text=True,
    )
    assert summary.returncode == 0, summary.stdout + summary.stderr
    assert summary.stdout.count("| PASS |") == 6


def run_shard(directory, index, count, plugin="scripts.ci.pytest_shard"):
    report = directory / f"shard-{index}.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            plugin,
            "-c",
            str(directory / "pytest.ini"),
            str(directory),
            "-m",
            "integration",
            "--ci-shard-index",
            str(index),
            "--ci-shard-count",
            str(count),
            f"--junitxml={report}",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": os.pathsep.join([str(directory), str(ROOT)]),
        },
        capture_output=True,
        text=True,
    )
    return result, report


def test_ci_shards_cover_every_selected_test_once_and_keep_files_together(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = integration\n")
    for number in range(12):
        (tmp_path / f"test_sample_{number}.py").write_text(
            "import pytest\nfrom pathlib import Path\n"
            "@pytest.fixture(scope='module', autouse=True)\n"
            "def once():\n"
            "    with Path(__file__).with_suffix('.fixture').open('a') as f: f.write('once\\n')\n"
            "@pytest.mark.integration\n"
            "@pytest.mark.parametrize('value', [1, 2])\n"
            "def test_selected(value): assert value > 0\n"
            "def test_unit(): raise AssertionError('must be deselected')\n"
        )
    all_cases = set()
    all_files = set()
    for index in range(6):
        result, report = run_shard(tmp_path, index, 6)
        assert result.returncode == 0, result.stdout + result.stderr
        reports.check_junit(report)
        cases = list(ET.parse(report).getroot().iter("testcase"))
        identities = {(case.get("classname"), case.get("name")) for case in cases}
        files = {case.get("classname") for case in cases}
        assert not all_cases & identities
        assert not all_files & files
        all_cases |= identities
        all_files |= files
    assert len(all_cases) == 24
    assert len(all_files) == 12
    assert all(path.read_text() == "once\n" for path in tmp_path.glob("*.fixture"))
    result, report = run_shard(tmp_path, 0, 1)
    assert result.returncode == 0, result.stdout + result.stderr
    unsharded = {
        (case.get("classname"), case.get("name"))
        for case in ET.parse(report).getroot().iter("testcase")
    }
    assert all_cases == unsharded


def test_native_workflow_requires_all_six_integration_artifacts():
    job = reader_jobs()["python-integration"]
    assert job["strategy"]["matrix"] == {"shard": list(range(6))}
    assert job["strategy"]["fail-fast"] is False
    assert job["with"]["test-command"] == (
        "bash scripts/ci/python-tests.sh integration ${{ matrix.shard }} 6"
    )
    assert "reports/python-integration-${{ matrix.shard }}.xml" in job["with"]["test-results-path"]
    assert (
        "reports/coverage/python-integration-${{ matrix.shard }}/coverage.db"
        in job["with"]["test-results-path"]
    )


def test_integration_measures_ci_utilities_without_changing_backend_unit_reports():
    script = (ROOT / "scripts/ci/python-tests.sh").read_text()
    assert script.count("backend_sources=$(") == 1
    assert script.index("backend_sources=$(") < script.index('if [[ "$suite" != integration ]]')
    assert script.count('--source="$backend_sources,scripts" -m pytest') == 2
    assert "backend_include='apps/*/src/**,packages/*/src/**'" in script
    assert script.count('--include="$backend_include"') == 4


def test_case_count_plan_balances_all_current_files_deterministically():
    weights = {"heavy.py": 60, "medium.py": 30, "new_a.py": 15, "new_b.py": 15, "light.py": 5}
    paths = [path for path, cases in weights.items() for _ in range(cases)]
    plan = shards.balanced_shards(paths, 3)
    assert plan == shards.balanced_shards(reversed(paths), 3)
    assert plan == {
        "heavy.py": 0,
        "medium.py": 1,
        "new_a.py": 2,
        "new_b.py": 2,
        "light.py": 1,
    }
    assert set(plan) == set(paths)
    loads = [sum(weights[path] for path in weights if plan[path] == i) for i in range(3)]
    assert loads == [60, 35, 30]
    assert shards.balanced_shards([], 3) == {}


def test_case_count_plan_uses_marker_and_keyword_filtered_collection_before_balancing(tmp_path):
    (tmp_path / "test_excluded.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', range(100))\n"
        "def test_selected_case(value): raise AssertionError('unit')\n"
    )
    for suffix, count in (("a", 3), ("b", 2), ("c", 1)):
        (tmp_path / f"test_selected_{suffix}.py").write_text(
            "import pytest\n@pytest.mark.integration\n"
            f"@pytest.mark.parametrize('value', range({count}))\n"
            "def test_selected_case(value): pass\n"
            + (
                "@pytest.mark.integration\n"
                "@pytest.mark.parametrize('value', range(100))\n"
                "def test_keyword_excluded(value): raise AssertionError('keyword')\n"
                if suffix == "b"
                else ""
            )
        )
    # Only the six selected cases influence the plan; excluded marker/keyword
    # cases must not shift a file into a different shard.
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers = integration\naddopts = -k test_selected_case\n"
    )
    for index, expected in enumerate(
        ({"test_selected_a": 3}, {"test_selected_b": 2, "test_selected_c": 1})
    ):
        result, report = run_shard(tmp_path, index, 2)
        assert result.returncode == 0, result.stdout + result.stderr
        cases = list(ET.parse(report).getroot().iter("testcase"))
        observed = {}
        for case in cases:
            name = case.get("classname")
            observed[name] = observed.get(name, 0) + 1
        assert observed == expected


@pytest.mark.parametrize("index,count", [(-1, 3), (3, 3), (0, 0)])
def test_ci_shards_reject_invalid_partition(tmp_path, index, count):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    result, _ = run_shard(tmp_path, index, count)
    assert result.returncode == 4
    assert "Require 0 <= ci-shard-index < ci-shard-count" in result.stderr


@pytest.mark.parametrize(
    "event,ref_type,sonar_expected",
    [
        ("pull_request", "branch", True),
        ("pull_request", "branch", False),  # Fork and Dependabot PRs have no token.
        ("push", "branch", True),
        ("push", "tag", False),
        ("workflow_dispatch", "tag", False),
        ("merge_group", "branch", False),
        ("schedule", "branch", False),
    ],
)
@pytest.mark.parametrize("comment_required", [False, True])
@pytest.mark.parametrize("browser_comment_required", [False, True])
def test_ci_required_accepts_only_the_expected_successes(
    event, ref_type, sonar_expected, comment_required, browser_comment_required
):
    # Execute the actual gate, including the mutually exclusive release/check jobs.
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    gate = textwrap.dedent(workflow.split("  required:\n", 1)[1].split("        run: |\n", 1)[1])
    release = event == "push" and ref_type == "tag"
    results = {
        "python-unit": {"result": "success"},
        "coverage-report": {"result": "success" if comment_required else "skipped"},
        "admin-web": {"result": "success"},
        "partner-web": {"result": "success"},
        "user-web": {"result": "success"},
        "extensions": {"result": "success"},
        "api-compat": {
            "result": "success" if event in {"pull_request", "merge_group"} else "skipped"
        },
        "api-fuzz": {
            "result": "success" if event in {"pull_request", "merge_group"} else "skipped"
        },
        "python-integration": {"result": "success"},
        "migration-upgrade": {"result": "success"},
        "reader-parity": {"result": "success"},
        "admin-browser": {"result": "success"},
        "live-browser": {"result": "success"},
        "security": {"result": "success"},
        "dast": {
            "result": "success" if event in {"pull_request", "merge_group", "push"} else "skipped"
        },
        "containers": {"result": "skipped" if release else "success"},
        "release-images": {"result": "success" if release else "skipped"},
        "performance": {"result": "success" if event == "pull_request" else "skipped"},
        "recovery": {
            "result": "success" if event in {"pull_request", "merge_group", "push"} else "skipped"
        },
        "browser-budgets": {
            "result": "success"
            if event in {"pull_request", "merge_group"}
            or (event == "push" and ref_type == "branch")
            else "skipped"
        },
        "mutation": {
            "result": "success"
            if event in {"pull_request", "merge_group"}
            or (event == "push" and ref_type == "branch")
            else "skipped"
        },
        "property": {
            "result": "success"
            if event in {"pull_request", "merge_group"}
            or (event == "push" and ref_type == "branch")
            else "skipped"
        },
        "sonarqube": {"result": "success" if sonar_expected else "skipped"},
        "browser-report": {"result": "success" if browser_comment_required else "skipped"},
    }
    declared_needs = (
        workflow.split("  required:\n", 1)[1]
        .split("    needs:\n", 1)[1]
        .split("    runs-on:", 1)[0]
    )
    assert {line.strip().removeprefix("- ") for line in declared_needs.splitlines()} == set(results)
    release_needs = workflow.split("  release-images:\n", 1)[1].split("    if:", 1)[0]
    assert "      - recovery\n" in release_needs

    def evaluate():
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", gate],
            env={
                **os.environ,
                "EVENT_NAME": event,
                "REF_TYPE": ref_type,
                "COVERAGE_COMMENT_REQUIRED": str(comment_required).lower(),
                "BROWSER_COMMENT_REQUIRED": str(browser_comment_required).lower(),
                "SONAR_EXPECTED": str(sonar_expected).lower(),
                "RESULTS": json.dumps(results),
            },
            capture_output=True,
            text=True,
        ).returncode

    assert evaluate() == 0
    for job in list(results):
        expected = results[job]["result"]
        for unexpected in {"success", "skipped", "failure", "cancelled"} - {expected}:
            results[job]["result"] = unexpected
            assert evaluate() != 0, (event, ref_type, job, unexpected)
        results[job]["result"] = expected
        del results[job]
        assert evaluate() != 0, (event, ref_type, job, "missing")
        results[job] = {"result": expected}


@pytest.mark.parametrize("status", ["failure", "error", "skipped"])
def test_junit_rejects_unsuccessful_tests(tmp_path, status):
    path = tmp_path / "test.xml"
    path.write_text(f"<testsuite><testcase><{status}/></testcase></testsuite>")
    with pytest.raises(ValueError):
        reports.check_junit(path)


def test_junit_rejects_empty_suite(tmp_path):
    path = tmp_path / "test.xml"
    path.write_text('<testsuite tests="100"/>')
    with pytest.raises(ValueError):
        reports.check_junit(path)


def test_junit_accepts_nested_passing_suites(tmp_path):
    path = tmp_path / "test.xml"
    path.write_text("<testsuites><testsuite><testcase/></testsuite></testsuites>")
    reports.check_junit(path)


def test_sarif_rejects_missing_reports(tmp_path):
    with pytest.raises(ValueError):
        reports.check_sarif(tmp_path)


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [{"results": [{"ruleId": "py/security-issue"}]}],
        [{"results": [], "invocations": [{"executionSuccessful": False}]}],
    ],
)
def test_sarif_rejects_findings_and_incomplete_analysis(tmp_path, runs):
    (tmp_path / "python.sarif").write_text(json.dumps({"runs": runs}))
    with pytest.raises(ValueError):
        reports.check_sarif(tmp_path)


def test_sarif_accepts_clean_analysis(tmp_path):
    (tmp_path / "python.sarif").write_text(json.dumps({"runs": [{"results": []}]}))
    reports.check_sarif(tmp_path)


def index(architectures):
    return {
        "manifests": [{"platform": {"os": "linux", "architecture": arch}} for arch in architectures]
    }


@pytest.mark.parametrize(
    "architectures", [[], ["amd64"], ["arm64", "arm64"], ["amd64", "arm64"], ["arm64", "386"]]
)
def test_image_index_rejects_missing_duplicate_or_extra_platforms(architectures):
    with pytest.raises(ValueError):
        images.check_index(index(architectures))


def test_image_index_accepts_arm64_with_buildkit_attestation():
    document = index(["arm64"])
    document["manifests"].append(
        {
            "platform": {"os": "unknown", "architecture": "unknown"},
            "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
        }
    )
    images.check_index(document)
