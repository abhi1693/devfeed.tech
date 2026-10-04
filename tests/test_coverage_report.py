"""Fail-closed coverage inventories, measured counters, and PR comment rendering."""

import importlib.util
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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


def run_comment(tmp_path, *, scenario="success"):
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    script = textwrap.dedent(
        workflow.split("          script: |\n", 1)[1].split("  admin-web:\n", 1)[0]
    )
    wrapper = """
      const fs = require('fs');
      const vm = require('vm');
      const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
      const calls = [];
      const head = input.stale ? 'new-head' : 'abcdef1234567890';
      let reads = 0;
      const github = {rest: {
        pulls: {get: async () => ({data: {head: {
          sha: input.lateStale && reads++ ? 'new-head' : head}}})},
        issues: {listComments: {}, createComment: async value => calls.push(['create', value]),
          updateComment: async value => calls.push(['update', value])}
      }, paginate: async () => input.previous ? [{id: 7,
        user: {type: input.human ? 'User' : 'Bot'},
        body: '<!-- devfeed-backend-unit-coverage -->\\nold'}] : []};
      const context = {repo: {owner: 'owner', repo: 'repo'},
        serverUrl: 'https://github.com', runId: 123,
        payload: {pull_request: {number: 4, head: {sha: 'abcdef1234567890'}}}};
      const sandbox = {require: () => ({readFileSync: () => JSON.stringify(input.summary)}),
        github, context,
        core: {setFailed: message => calls.push(['failed', message])},
        process: {env: {UNIT_RESULT: input.result, GITHUB_RUN_ATTEMPT: '2'}}};
      vm.runInNewContext('(async () => {' + input.script + '})()', sandbox)
        .then(() => console.log(JSON.stringify(calls)))
        .catch(error => {console.error(error); process.exit(1);});
    """
    workspaces = [
        "apps/admin-api",
        "apps/aggregator",
        "apps/api",
        "apps/article-enrichment-worker",
        "apps/cli",
        "apps/images-worker",
        "apps/mcp",
        "apps/notifications",
        "apps/search-indexer",
        "apps/source-discovery-worker",
        "apps/user-api",
        "packages/core",
        "packages/http",
    ]
    counters = {"covered_lines": 3, "num_statements": 4, "covered_branches": 1, "num_branches": 2}
    summary = {
        "schema": 1,
        "valid": True,
        "status": "passed",
        "minimum": 65,
        "unit_exit_code": 0,
        "source_files": 270,
        "totals": {key: value * 13 for key, value in counters.items()},
        "packages": dict.fromkeys(workspaces, counters),
        "tests": {"total": 2, "passed": 2, "failed": 0, "skipped": 0},
    }
    if scenario == "malformed":
        summary["totals"]["covered_lines"] = "@everyone <script>"
    elif scenario == "missing":
        summary = {"schema": 1, "valid": False, "status": "incomplete"}
    elif scenario == "below-floor":
        summary["minimum"] = 80
    elif scenario == "pytest-failed":
        summary["unit_exit_code"] = 1
    elif scenario == "mismatched":
        summary["totals"]["covered_lines"] = 1
    input_path, wrapper_path = tmp_path / "input.json", tmp_path / "comment.cjs"
    input_path.write_text(
        json.dumps(
            {
                "script": script,
                "summary": summary,
                "result": "failure" if scenario == "job-failed" else "success",
                "stale": scenario == "stale",
                "lateStale": scenario == "late-stale",
                "previous": scenario in {"previous", "human"},
                "human": scenario == "human",
            }
        )
    )
    wrapper_path.write_text(textwrap.dedent(wrapper))
    result = subprocess.run(
        ["node", str(wrapper_path), str(input_path)], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("scenario", ["success", "previous", "human"])
def test_pr_report_uses_measured_counts_and_updates_one_bot_comment(tmp_path, scenario):
    calls = run_comment(tmp_path, scenario=scenario)
    assert len(calls) == 1 and calls[0][0] == ("update" if scenario == "previous" else "create")
    body = calls[0][1]["body"]
    assert "**PASS**" in body and "66.67%" in body and "65.00%" in body
    assert "2 passed" in body and "abcdef123456" in body
    assert "packages/core" in body and "actions/runs/123" in body


@pytest.mark.parametrize("scenario", ["below-floor", "pytest-failed", "job-failed"])
def test_pr_report_never_claims_success_for_a_failed_gate(tmp_path, scenario):
    assert "**FAIL**" in run_comment(tmp_path, scenario=scenario)[0][1]["body"]


@pytest.mark.parametrize("scenario", ["missing", "malformed", "mismatched"])
def test_invalid_reports_have_fixed_text_and_cannot_inject_comment_content(tmp_path, scenario):
    calls = run_comment(tmp_path, scenario=scenario)
    body = calls[0][1]["body"]
    assert "Coverage unavailable" in body and "**PASS**" not in body
    assert "@everyone" not in body and "<script>" not in body
    assert calls[1] == ["failed", "Backend coverage artifact is missing or invalid"]


@pytest.mark.parametrize("scenario", ["stale", "late-stale"])
def test_superseded_pr_head_receives_no_comment(tmp_path, scenario):
    assert run_comment(tmp_path, scenario=scenario) == []
