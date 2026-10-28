"""Regression checks for the release pipeline's fail-closed report gates."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET

import pytest
import yaml

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT
BACKEND_UNIT_JOBS = {"python-lint", "python-unit", "python-loadtest"}
FRONTEND_UNIT_JOBS = {"admin-web", "partner-web", "user-web", "extensions"}
BACKEND_INTEGRATION_JOBS = {
    "python-integration",
    "migration-upgrade",
    "api-fuzz",
    "recovery",
}
FRONTEND_INTEGRATION_JOBS = {
    "reader-browser",
    "admin-browser",
    "partner-browser",
    "property",
    "mutation",
}
SHARED_INTEGRATION_JOBS = {"live-browser", "dast", "security"}


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reports = load_script("check_reports")
images = load_script("check_image_index")
shards = load_script("pytest_shard")
READER_SUITES = (
    "web",
    "chrome",
    "edge",
    "partner-web",
    "partner-chrome",
    "partner-edge",
)
READER_LABELS = (
    "Web",
    "Chrome extension",
    "Edge extension",
    "Partner web",
    "Partner Chrome",
    "Partner Edge",
)
READER_ATTEMPT = "2"
PARTNER_IPS = {"web": "203.0.113.20", "chrome": "203.0.113.30", "edge": "203.0.113.40"}
PARTNER_PATHS = ("/v1/partner-placements/query", "/v1/partner-tracking/events")
PARTNER_CHECKPOINTS = [
    "history-isolation",
    "feed-continuity",
    "mounted-expiry",
    "gateway-caller-proofs",
    "full-placement-matrix",
]
READER_SHA = "a" * 40


def reader_jobs():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]


def job_needs(job):
    dependencies = job.get("needs", [])
    return {dependencies} if isinstance(dependencies, str) else set(dependencies)


def job_ancestors(jobs, name, visiting=frozenset()):
    assert name not in visiting, f"Cyclic CI dependency: {sorted(visiting)} -> {name}"
    assert name in jobs, f"Unknown CI dependency: {name}"
    dependencies = job_needs(jobs[name])
    return dependencies.union(
        *(job_ancestors(jobs, parent, visiting | {name}) for parent in dependencies)
    )


def test_ci_job_dependencies_form_a_complete_acyclic_graph():
    jobs = reader_jobs()
    for name in jobs:
        job_ancestors(jobs, name)


def test_backend_and_frontend_unit_lanes_start_independently():
    jobs = reader_jobs()
    for name in BACKEND_UNIT_JOBS:
        assert job_ancestors(jobs, name).isdisjoint(FRONTEND_UNIT_JOBS)
    for name in FRONTEND_UNIT_JOBS:
        assert job_ancestors(jobs, name).isdisjoint(BACKEND_UNIT_JOBS)
    partner_scripts = json.loads(jobs["partner-web"]["with"]["scripts"])
    assert partner_scripts["build"] == "partner:build", "Browser checks belong after unit tests"


@pytest.mark.parametrize("name", sorted(BACKEND_INTEGRATION_JOBS))
def test_backend_integration_waits_for_backend_units_without_waiting_for_frontend(name):
    jobs = reader_jobs()
    ancestors = job_ancestors(jobs, name)
    assert ancestors >= BACKEND_UNIT_JOBS
    assert ancestors.isdisjoint(FRONTEND_UNIT_JOBS)


@pytest.mark.parametrize("name", sorted(FRONTEND_INTEGRATION_JOBS))
def test_frontend_integration_waits_for_frontend_units_without_waiting_for_backend(name):
    jobs = reader_jobs()
    ancestors = job_ancestors(jobs, name)
    assert ancestors >= FRONTEND_UNIT_JOBS
    assert ancestors.isdisjoint(BACKEND_UNIT_JOBS)


@pytest.mark.parametrize("name", sorted(SHARED_INTEGRATION_JOBS))
def test_live_boundary_integration_waits_for_both_unit_lanes(name):
    jobs = reader_jobs()
    assert job_ancestors(jobs, name) >= BACKEND_UNIT_JOBS | FRONTEND_UNIT_JOBS


@pytest.mark.parametrize("name", ["containers"])
def test_image_work_starts_only_after_all_validation_lanes(name):
    jobs = reader_jobs()
    assert (
        BACKEND_UNIT_JOBS
        | FRONTEND_UNIT_JOBS
        | BACKEND_INTEGRATION_JOBS
        | FRONTEND_INTEGRATION_JOBS
        | SHARED_INTEGRATION_JOBS
        | {"reader-parity", "sonarqube"}
    ) <= job_ancestors(jobs, name)


def image_job_scheduled(job, results, event, ref_type, *, cancelled=False):
    """Execute the workflow's boolean condition, including GitHub's default status check."""
    condition = job["if"].strip().removeprefix("${{").removesuffix("}}").strip()
    statuses = re.findall(r"\b(always|cancelled|success|failure)\(\)", condition)
    # The expressions used here contain only status functions, context values,
    # string comparisons and boolean operators, which have equivalent jq forms.
    expression = re.sub(r"'([^']*)'", lambda match: json.dumps(match[1]), condition)
    expression = expression.replace("always()", "true")
    for status in ("cancelled", "success", "failure"):
        expression = expression.replace(f"{status}()", f"${status}")
    expression = re.sub(r"!\s*(\$(?:cancelled|success|failure)|true)", r"(\1 | not)", expression)
    expression = expression.replace("github.event_name", "$event").replace(
        "github.ref_type", "$ref_type"
    )
    expression = re.sub(
        r"needs\.([\w-]+)\.result", lambda match: f".[{json.dumps(match[1])}].result", expression
    )
    expression = expression.replace("&&", "and").replace("||", "or")
    if not statuses:
        # A skipped ancestor blocks success(), even when its aggregate gate passes.
        expression = f"$success and ({expression})"
    completed = subprocess.run(
        [
            "jq",
            "-e",
            "--arg",
            "event",
            event,
            "--arg",
            "ref_type",
            ref_type,
            "--argjson",
            "cancelled",
            json.dumps(cancelled),
            "--argjson",
            "success",
            json.dumps(all(item["result"] == "success" for item in results.values())),
            "--argjson",
            "failure",
            json.dumps(any(item["result"] == "failure" for item in results.values())),
            expression,
        ],
        input=json.dumps(results),
        capture_output=True,
        text=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr
    return completed.returncode == 0


@pytest.mark.parametrize("name", ["containers"])
@pytest.mark.parametrize(
    "event,ref_type,sonar_expected",
    [
        ("pull_request", "branch", True),
        ("pull_request", "branch", False),
        ("merge_group", "branch", False),
        ("push", "branch", True),
        ("push", "tag", False),
        ("workflow_dispatch", "tag", False),
        ("schedule", "branch", False),
    ],
)
def test_image_stages_run_after_successful_validation_despite_expected_ancestor_skips(
    name, event, ref_type, sonar_expected
):
    jobs = reader_jobs()
    ancestors = job_ancestors(jobs, name)
    optional = {
        "api-fuzz": event in {"pull_request", "merge_group"},
        "recovery": event in {"pull_request", "merge_group", "push"},
        "dast": event in {"pull_request", "merge_group", "push"},
        "sonarqube": sonar_expected,
        **dict.fromkeys(
            ("property", "mutation"),
            event in {"pull_request", "merge_group"} or (event == "push" and ref_type == "branch"),
        ),
    }
    results = {
        parent: {"result": "success" if optional.get(parent, True) else "skipped"}
        for parent in ancestors
    }
    assert results["validation"]["result"] == "success"
    if event != "pull_request" or not sonar_expected:
        assert any(item["result"] == "skipped" for item in results.values())
    assert image_job_scheduled(jobs[name], results, event, ref_type)
    assert jobs["containers"]["with"]["publish"] is False


@pytest.mark.parametrize("name", ["containers"])
def test_image_stages_reject_failed_cancelled_skipped_or_missing_prerequisites(name):
    jobs = reader_jobs()
    job = jobs[name]
    results = {parent: {"result": "success"} for parent in job_ancestors(jobs, name)}
    ref_type = "branch"
    assert image_job_scheduled(job, results, "push", ref_type)
    assert not image_job_scheduled(job, results, "push", ref_type, cancelled=True)
    for parent in job_needs(job):
        for result in ("failure", "cancelled", "skipped", ""):
            results[parent] = {"result": result}
            assert not image_job_scheduled(job, results, "push", ref_type), (name, parent, result)
        del results[parent]
        assert not image_job_scheduled(job, results, "push", ref_type), (name, parent, "missing")
        results[parent] = {"result": "success"}


@pytest.mark.parametrize(
    "name",
    sorted(BACKEND_INTEGRATION_JOBS | FRONTEND_INTEGRATION_JOBS | SHARED_INTEGRATION_JOBS)
    + ["containers"],
)
def test_downstream_work_cannot_override_failed_dependencies(name):
    job = reader_jobs()[name]
    condition = job.get("if", "")
    # GitHub adds success() to ordinary event conditions. A status function
    # disables that safeguard, so every direct prerequisite then needs a guard.
    if any(status in condition for status in ("always()", "cancelled()", "failure()")):
        for parent in job_needs(job):
            assert f"needs.{parent}.result == 'success'" in condition, (name, parent)


@pytest.mark.parametrize(
    "name,workers,parallel",
    [("python-unit-shards", 4, 4), ("python-integration", 6, 4), ("reader-browser", 6, 3)],
)
def test_scaled_worker_pools_keep_every_partition_and_bound_parallelism(name, workers, parallel):
    job = reader_jobs()[name]
    strategy = job["strategy"]
    assert strategy["fail-fast"] is False, "Failed partitions must leave the other workers running"
    assert strategy["max-parallel"] == parallel
    matrix = strategy["matrix"]
    assert len(matrix.get("include", matrix.get("shard", []))) == workers


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
            json.dumps(
                {
                    "suite": suite,
                    "sha": READER_SHA,
                    "attempt": READER_ATTEMPT,
                    "status": "success",
                }
            )
        )
        if suite.startswith("partner-"):
            runtime = suite.removeprefix("partner-")
            proof = directory / "partner-placements" / f"runtime-proof-{runtime}.json"
            proof.parent.mkdir()
            proof.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "source_sha": READER_SHA,
                        "attempt": READER_ATTEMPT,
                        "completed": True,
                        "gateway_process_id": 123,
                        "full_runtime_count": 1,
                        "caller_ips": list(PARTNER_IPS.values()),
                        "caller_probes": [
                            {
                                "caller": caller,
                                "caller_proofs": {path: [ip] for path in PARTNER_PATHS},
                            }
                            for caller, ip in PARTNER_IPS.items()
                        ],
                        "full_runtime_checks": [
                            {
                                "runtime": runtime,
                                "caller_proofs": {
                                    path: [PARTNER_IPS[runtime]] for path in PARTNER_PATHS
                                },
                                "checkpoints": PARTNER_CHECKPOINTS,
                            }
                        ],
                    }
                )
            )
        if suite in ("web", "chrome", "edge"):
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
        [
            "bash",
            "-e",
            "-o",
            "pipefail",
            "-c",
            reader_step("Require all reader suites")["run"],
        ],
        cwd=directory,
        env={
            **os.environ,
            "READER_RESULT": result,
            "SOURCE_SHA": READER_SHA,
            "SOURCE_ATTEMPT": READER_ATTEMPT,
        },
        capture_output=True,
        text=True,
    )


def test_reader_matrix_keeps_all_suites_and_strict_aggregate_and_report_boundaries():
    jobs = reader_jobs()
    worker = jobs["reader-browser"]
    assert worker["strategy"]["fail-fast"] is False
    assert worker["strategy"]["matrix"] == {
        "include": [
            {"suite": suite, "label": label}
            for suite, label in zip(READER_SUITES, READER_LABELS, strict=True)
        ]
    }
    assert worker["name"] == "Reader browser (${{ matrix.label }})"
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
    assert aggregate["name"] == "Reader browser gate"
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
        "SOURCE_ATTEMPT": "${{ github.run_attempt }}",
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
    assert "reader-parity" in job_ancestors(jobs, "containers")
    assert jobs["admin-browser"]["name"] == "Admin browser tests"
    assert jobs["live-browser"]["name"] == "Live API browser tests"
    security = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text())
    dispatch = next(
        step
        for step in security["jobs"]["configuration"]["steps"]
        if step.get("name") == "Test reader CI dispatch"
    )
    assert dispatch["run"] == "node --test tests/reader-parity.test.mjs"
    assert "if" not in dispatch and "continue-on-error" not in dispatch


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
            "GITHUB_RUN_ATTEMPT": READER_ATTEMPT,
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
            "attempt": READER_ATTEMPT,
            "status": "success",
        }


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_reader_aggregate_rejects_unsuccessful_matrix_even_with_complete_reports(
    reader_reports, result
):
    assert run_reader_gate(reader_reports, result).returncode != 0


@pytest.mark.parametrize("suite", READER_SUITES)
@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "invalid-json",
        "wrong-suite",
        "stale-sha",
        "stale-attempt",
        "missing-attempt",
        "failed",
    ],
)
def test_reader_aggregate_rejects_missing_stale_or_unsuccessful_suite_reports(
    reader_reports, suite, defect
):
    marker = reader_reports / "reader-reports" / suite / "reports/reader-parity-result.json"
    if defect == "missing":
        marker.unlink()
    elif defect == "invalid-json":
        marker.write_text("not-json")
    elif defect == "missing-attempt":
        data = json.loads(marker.read_text())
        del data["attempt"]
        marker.write_text(json.dumps(data))
    else:
        data = json.loads(marker.read_text())
        key, value = {
            "wrong-suite": ("suite", "unexpected"),
            "stale-sha": ("sha", "b" * 40),
            "stale-attempt": ("attempt", "1"),
            "failed": ("status", "failure"),
        }[defect]
        marker.write_text(json.dumps({**data, key: value}))
    result = run_reader_gate(reader_reports)
    assert result.returncode != 0
    assert f"Missing or unsuccessful current-source report: {suite}" in result.stdout


@pytest.mark.parametrize("runtime", ["web", "chrome", "edge"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "malformed",
        "stale-sha",
        "stale-attempt",
        "unfinished",
        "wrong-runtime",
        "missing-probe",
        "cached-first-caller",
        "missing-tracking",
        "missing-checkpoint",
        "missing-gateway",
        "wrong-global-callers",
        "extra-full-runtime",
    ],
)
def test_reader_gate_requires_complete_same_gateway_browser_evidence(
    reader_reports, runtime, defect
):
    report = (
        reader_reports
        / f"reader-reports/partner-{runtime}/reports/partner-placements"
        / f"runtime-proof-{runtime}.json"
    )
    proof = json.loads(report.read_text())
    if defect == "missing":
        report.unlink()
    elif defect == "malformed":
        report.write_text("not-json")
    else:
        if defect == "stale-sha":
            proof["source_sha"] = "b" * 40
        elif defect == "stale-attempt":
            proof["attempt"] = "1"
        elif defect == "unfinished":
            proof["completed"] = False
        elif defect == "wrong-runtime":
            proof["full_runtime_checks"][0]["runtime"] = "unknown"
        elif defect == "missing-probe":
            proof["caller_probes"].pop()
        elif defect == "cached-first-caller":
            # Three independent workers/union counts still look complete. The SAME
            # gateway's second browser caller must not reuse its first caller IP.
            proof["caller_probes"][1]["caller_proofs"] = proof["caller_probes"][0]["caller_proofs"]
        elif defect == "missing-tracking":
            del proof["caller_probes"][1]["caller_proofs"][PARTNER_PATHS[1]]
        elif defect == "missing-checkpoint":
            proof["full_runtime_checks"][0]["checkpoints"].pop()
        elif defect == "missing-gateway":
            del proof["gateway_process_id"]
        elif defect == "wrong-global-callers":
            proof["caller_ips"] = [PARTNER_IPS[runtime]]
        elif defect == "extra-full-runtime":
            proof["full_runtime_checks"].append(proof["full_runtime_checks"][0])
        report.write_text(json.dumps(proof))
    result = run_reader_gate(reader_reports)
    assert result.returncode != 0
    assert f"Missing or incomplete same-gateway partner proof: {runtime}" in result.stdout


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
        env={
            **os.environ,
            "GITHUB_STEP_SUMMARY": str(reader_reports / "step-summary.md"),
        },
        capture_output=True,
        text=True,
    )
    assert summary.returncode != 0
    assert f"| {surface} | {state} | MISSING |" in summary.stdout


def test_reader_aggregate_accepts_current_successes_and_copies_all_six_summaries(
    reader_reports,
):
    result = run_reader_gate(reader_reports)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = subprocess.run(
        ["node", str(ROOT / "scripts/ci/accessibility-report.mjs")],
        cwd=reader_reports,
        env={
            **os.environ,
            "GITHUB_STEP_SUMMARY": str(reader_reports / "step-summary.md"),
        },
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


def test_integration_measures_ci_utilities_and_migrations_without_changing_backend_unit_reports():
    script = (ROOT / "scripts/ci/python-tests.sh").read_text()
    assert script.count("backend_sources=$(") == 1
    assert script.index("backend_sources=$(") < script.index('if [[ "$suite" != integration ]]')
    assert script.count('--source="$backend_sources,scripts,migrations,loadtests" -m pytest') == 3
    assert "backend_include='apps/*/src/**,packages/*/src/**'" in script
    assert script.count('--include="$backend_include"') == 4


def test_real_loadtest_contracts_use_an_isolated_environment_and_feed_sonar_coverage():
    jobs = reader_jobs()
    contracts = jobs["python-loadtest"]
    assert "--group loadtest" in contracts["with"]["sync-command"]
    assert "--group loadtest" not in jobs["python-unit-shards"]["with"]["sync-command"]
    assert "if" not in contracts and "continue-on-error" not in contracts
    assert contracts["with"]["test-command"].replace("\\\n", "").split() == [
        "mkdir",
        "-p",
        "reports/coverage/python-loadtest",
        "COVERAGE_FILE=reports/coverage/python-loadtest/coverage.db",
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "--no-build",
        "coverage",
        "run",
        "--source=loadtests",
        "-m",
        "unittest",
        "discover",
        "-s",
        "loadtests",
        "-p",
        "'test_*.py'",
    ]
    assert contracts["with"]["test-results-path"] == "reports/"
    assert contracts["with"]["test-results-name"] == (
        "python-loadtest-arm64-${{ github.run_attempt }}"
    )
    assert all(
        "python-loadtest" in job_ancestors(jobs, name)
        for name in ("sonarqube", "required", "containers")
    )
    download = next(
        step
        for step in jobs["sonarqube"]["steps"]
        if step.get("name") == "Download Python coverage from this run"
    )
    assert download["with"]["pattern"] == "python-*-arm64-${{ github.run_attempt }}"
    assert "needs.python-loadtest.result == 'success'" in jobs["sonarqube"]["if"]


def test_unit_shards_merge_into_the_single_canonical_coverage_artifact():
    jobs = reader_jobs()
    workers = jobs["python-unit-shards"]
    assert workers["strategy"]["matrix"] == {"shard": list(range(4))}
    assert workers["with"]["test-command"] == (
        "bash scripts/ci/python-tests.sh unit ${{ matrix.shard }} 4"
    )
    raw_artifact = workers["with"]["test-results-name"]
    assert raw_artifact == "unit-shard-${{ matrix.shard }}-arm64-${{ github.run_attempt }}"
    assert not raw_artifact.startswith("python-"), "Sonar must only combine the validated aggregate"
    aggregate = jobs["python-unit"]
    assert job_needs(aggregate) == {"python-unit-shards"}
    download = next(
        step for step in aggregate["steps"] if "download-artifact@" in step.get("uses", "")
    )
    assert download["with"] == {
        "pattern": "unit-shard-*-arm64-${{ github.run_attempt }}",
        "path": "reports/",
        "merge-multiple": True,
    }
    assert "continue-on-error" not in download
    upload = next(step for step in aggregate["steps"] if "upload-artifact@" in step.get("uses", ""))
    assert upload["with"]["name"] == "python-unit-arm64-${{ github.run_attempt }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "reports/python-unit.xml" in upload["with"]["path"]
    assert "reports/coverage/python-unit/coverage.db" in upload["with"]["path"]
    assert "reports/coverage/summary.json" in upload["with"]["path"]


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
def test_unit_aggregate_never_promotes_reports_from_unsuccessful_workers(tmp_path, result):
    script = tmp_path / "scripts/ci/python-tests.sh"
    script.parent.mkdir(parents=True)
    script.write_text('set -e\ntest "$*" = "unit-merge 0 4"\nprintf merged > combined\n')
    aggregate = reader_jobs()["python-unit"]
    check = next(
        step
        for step in aggregate["steps"]
        if step.get("name") == "Validate unit shards and enforce combined coverage"
    )
    assert check["env"] == {"UNIT_SHARDS_RESULT": "${{ needs.python-unit-shards.result }}"}
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", check["run"]],
        cwd=tmp_path,
        env={**os.environ, "UNIT_SHARDS_RESULT": result},
        capture_output=True,
        text=True,
    )
    assert (completed.returncode == 0) is (result == "success"), completed.stderr
    assert (tmp_path / "combined").exists() is (result == "success")


def test_case_count_plan_balances_all_current_files_deterministically():
    weights = {
        "heavy.py": 60,
        "medium.py": 30,
        "new_a.py": 15,
        "new_b.py": 15,
        "light.py": 5,
    }
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


def test_case_count_plan_uses_marker_and_keyword_filtered_collection_before_balancing(
    tmp_path,
):
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
    "name",
    ["backend-unit", "frontend-unit", "backend-integration", "frontend-integration", "validation"],
)
@pytest.mark.parametrize(
    "event,ref_type,sonar_expected",
    [
        ("pull_request", "branch", True),
        ("pull_request", "branch", False),
        ("push", "branch", True),
        ("push", "tag", False),
        ("workflow_dispatch", "tag", False),
        ("merge_group", "branch", False),
        ("schedule", "branch", False),
    ],
)
def test_stage_gates_reject_failed_cancelled_missing_and_unexpectedly_skipped_dependencies(
    name, event, ref_type, sonar_expected
):
    gate = reader_jobs()[name]
    assert gate["if"] == "always() && !cancelled()"
    scripts = [step["run"] for step in gate["steps"] if "run" in step]
    assert len(scripts) == 1
    # Intentional event skips must be accepted; a skipped applicable check must
    # block later work just as a failure does. Execute the actual workflow gate.
    applicable = {
        "api-fuzz": event in {"pull_request", "merge_group"},
        "recovery": event in {"pull_request", "merge_group", "push"},
        "dast": event in {"pull_request", "merge_group", "push"},
        "sonarqube": sonar_expected,
        **dict.fromkeys(
            ("property", "mutation"),
            event in {"pull_request", "merge_group"} or (event == "push" and ref_type == "branch"),
        ),
    }
    results = {
        parent: {"result": "success" if applicable.get(parent, True) else "skipped"}
        for parent in job_needs(gate)
    }

    def evaluate():
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", scripts[0]],
            env={
                **os.environ,
                "EVENT_NAME": event,
                "REF_TYPE": ref_type,
                "SONAR_EXPECTED": str(sonar_expected).lower(),
                "RESULTS": json.dumps(results),
            },
            capture_output=True,
            text=True,
        )

    completed = evaluate()
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for parent in list(results):
        expected = results[parent]["result"]
        for unexpected in {"success", "failure", "cancelled", "skipped"} - {expected}:
            results[parent] = {"result": unexpected}
            assert evaluate().returncode != 0, (name, event, ref_type, parent, unexpected)
        del results[parent]
        assert evaluate().returncode != 0, (name, event, ref_type, parent, "missing")
        results[parent] = {"result": expected}


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
def test_ci_required_accepts_only_the_expected_successes(
    event, ref_type, sonar_expected, comment_required
):
    # Execute the actual master/PR gate without a tag-publishing bypass.
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    gate = textwrap.dedent(workflow.split("  required:\n", 1)[1].split("        run: |\n", 1)[1])
    results = {
        "python-lint": {"result": "success"},
        "python-unit-shards": {"result": "success"},
        "python-unit": {"result": "success"},
        "python-loadtest": {"result": "success"},
        "backend-unit": {"result": "success"},
        "frontend-unit": {"result": "success"},
        "backend-integration": {"result": "success"},
        "frontend-integration": {"result": "success"},
        "validation": {"result": "success"},
        "coverage-report": {"result": "success" if comment_required else "skipped"},
        "admin-web": {"result": "success"},
        "partner-web": {"result": "success"},
        "user-web": {"result": "success"},
        "extensions": {"result": "success"},
        "api-fuzz": {
            "result": "success" if event in {"pull_request", "merge_group"} else "skipped"
        },
        "python-integration": {"result": "success"},
        "migration-upgrade": {"result": "success"},
        "reader-parity": {"result": "success"},
        "admin-browser": {"result": "success"},
        "partner-browser": {"result": "success"},
        "live-browser": {"result": "success"},
        "security": {"result": "success"},
        "dast": {
            "result": "success" if event in {"pull_request", "merge_group", "push"} else "skipped"
        },
        "containers": {"result": "success"},
        "recovery": {
            "result": "success" if event in {"pull_request", "merge_group", "push"} else "skipped"
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
    }
    declared_needs = (
        workflow.split("  required:\n", 1)[1]
        .split("    needs:\n", 1)[1]
        .split("    runs-on:", 1)[0]
    )
    assert {line.strip().removeprefix("- ") for line in declared_needs.splitlines()} == set(results)
    assert "recovery" in job_ancestors(reader_jobs(), "containers")

    def evaluate():
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", gate],
            env={
                **os.environ,
                "EVENT_NAME": event,
                "REF_TYPE": ref_type,
                "COVERAGE_COMMENT_REQUIRED": str(comment_required).lower(),
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
    "architectures",
    [[], ["amd64"], ["arm64", "arm64"], ["amd64", "arm64"], ["arm64", "386"]],
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
