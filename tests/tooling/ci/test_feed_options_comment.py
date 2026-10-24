"""Exercise the real publisher without allowing artifacts to execute with write access."""

import json
import subprocess

import pytest
import yaml

from tests.support.paths import REPO_ROOT

MARKER = "<!-- devfeed-feed-options-scale -->"
HEAD = "a" * 40
BASE = "b" * 40
SUMMARY = (
    "| Scenario | Before | After | Delta | Improvement |\n"
    "| --- | ---: | ---: | ---: | ---: |\n"
    "| 1m English | 166.76 ms | 54.73 ms | -112.03 ms | +67.2% |\n"
)


def workflow_jobs():
    path = REPO_ROOT / ".github/workflows/feed-options-performance.yml"
    return yaml.safe_load(path.read_text())["jobs"]


def publish_comment(**settings):
    source = next(
        step["with"]["script"]
        for step in workflow_jobs()["report"]["steps"]
        if "script" in step.get("with", {})
    )
    harness = r"""
const assert = require('node:assert/strict');
const input = JSON.parse(process.argv[1]);
const head = 'a'.repeat(40), base = 'b'.repeat(40);
const marker = '<!-- devfeed-feed-options-scale -->';
const result = {writes: [], failures: [], warnings: [], pullReads: 0};
const report = {candidate_sha: head, baseline_sha: base,
  cases: [{equivalent: true}], expected_cases: 1,
  ...(input.completed ? {completed_at: '2026-10-24T10:08:03Z'} : {}), ...input.report};
if (input.missingCases) delete report.cases;
if (input.missingExpectedCases) delete report.expected_cases;
const files = {
  'report/results.json': input.malformed ? 'invalid JSON' : JSON.stringify(report),
  'report/summary.md': input.summary,
};
if (input.missing) delete files[`report/${input.missing}`];
const fs = {
  lstatSync(file) {
    if (!(file in files)) throw new Error('Artifact data unavailable');
    return {isFile: () => !(input.symlink && file.endsWith('summary.md')),
      size: input.oversized && file.endsWith('summary.md')
        ? 50001 : Buffer.byteLength(files[file])};
  },
  readFileSync(file, encoding) {
    assert.equal(encoding, 'utf8');
    return files[file];
  },
};
const context = {
  payload: {pull_request: {number: 303, head: {sha: head}, base: {sha: base}}},
  repo: {owner: 'abhi1693', repo: 'devfeed.tech'},
  serverUrl: 'https://github.com', runId: 200,
};
const previous = input.previous ? [{id: 900, user: {type: input.author ?? 'Bot'},
  body: input.previousBody ?? `${marker}\nOld successful results`}] : [];
const github = {
  rest: {
    pulls: {get: async request => {
      assert.equal(request.pull_number, 303);
      result.pullReads += 1;
      const stale = input.staleFirst || (input.staleLast && result.pullReads > 1);
      return {data: {state: input.closed ? 'closed' : 'open',
        head: {sha: stale ? 'c'.repeat(40) : head}}};
    }},
    issues: {
      listComments: () => {},
      createComment: async request => {result.writes.push({method: 'create', ...request});},
      updateComment: async request => {result.writes.push({method: 'update', ...request});},
    },
  },
  paginate: async (method, request) => {
    assert.equal(method, github.rest.issues.listComments);
    assert.equal(request.issue_number, 303);
    return previous;
  },
};
const core = {
  warning: message => result.warnings.push(message),
  setFailed: message => result.failures.push(message),
};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
new AsyncFunction('require', 'context', 'github', 'core', 'process', input.source)(
  name => {assert.equal(name, 'fs'); return fs;}, context, github, core,
  {env: {MEASUREMENT_RESULT: input.status, GITHUB_RUN_ATTEMPT: '2'}},
).then(() => process.stdout.write(JSON.stringify(result)))
 .catch(error => {console.error(error); process.exitCode = 1;});
"""
    completed = subprocess.run(
        [
            "node",
            "-e",
            harness,
            json.dumps(
                {
                    "source": source,
                    "summary": SUMMARY,
                    "status": "success",
                    "completed": True,
                    **settings,
                }
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(completed.stdout)


def test_complete_comment_preserves_readable_results_and_revision_provenance():
    result = publish_comment()
    assert not result["failures"]
    assert not result["warnings"]
    assert result["pullReads"] == 2
    comment = result["writes"][0]
    assert comment["method"] == "create"
    assert comment["issue_number"] == 303
    body = comment["body"]
    assert body.startswith(MARKER)
    assert "**COMPLETE**" in body
    assert SUMMARY.strip() in body
    assert f"/commit/{HEAD}" in body
    assert f"/commit/{BASE}" in body
    assert "/actions/runs/200" in body
    assert "attempt 2" in body


@pytest.mark.parametrize("previous", [False, True])
def test_failed_measurement_keeps_partial_evidence_without_a_passing_verdict(previous):
    result = publish_comment(status="failure", completed=False, previous=previous)
    assert not result["failures"]
    comment = result["writes"][0]
    assert comment["method"] == ("update" if previous else "create")
    if previous:
        assert comment["comment_id"] == 900
    body = comment["body"]
    assert "**PARTIAL / FAILED RUN**" in body
    assert "do not establish a passing full benchmark" in body
    assert SUMMARY.strip() in body
    assert "Old successful results" not in body
    assert "**COMPLETE**" not in body


@pytest.mark.parametrize("missing", ["summary.md", "results.json"])
@pytest.mark.parametrize("status", ["success", "failure", "skipped"])
def test_missing_reports_replace_stale_bot_success_with_unavailable_status(missing, status):
    result = publish_comment(missing=missing, status=status, previous=True)
    comment = result["writes"][0]
    assert comment["method"] == "update"
    assert comment["comment_id"] == 900
    assert "**RESULTS UNAVAILABLE**" in comment["body"]
    assert "not a passing benchmark" in comment["body"]
    assert "Old successful results" not in comment["body"]
    assert SUMMARY.strip() not in comment["body"]
    assert bool(result["failures"]) == (status == "success")


@pytest.mark.parametrize(
    "settings",
    [
        {"report": {"candidate_sha": "c" * 40}},
        {"report": {"baseline_sha": "c" * 40}},
        {"malformed": True},
        {"summary": ""},
        {"symlink": True},
        {"oversized": True},
    ],
)
def test_invalid_artifacts_cannot_republish_a_successful_report(settings):
    result = publish_comment(previous=True, **settings)
    assert result["failures"] == ["Successful benchmark job has missing or incomplete results"]
    body = result["writes"][0]["body"]
    assert "**RESULTS UNAVAILABLE**" in body
    assert "Old successful results" not in body


@pytest.mark.parametrize("settings", [{"completed": False}, {"report": {"completed_at": "bad"}}])
def test_incomplete_data_cannot_be_marked_complete_even_if_the_measure_job_passed(settings):
    result = publish_comment(previous=True, **settings)
    assert result["failures"]
    body = result["writes"][0]["body"]
    assert "**PARTIAL / FAILED RUN**" in body
    assert "**COMPLETE**" not in body


@pytest.mark.parametrize(
    "settings",
    [
        {"missingCases": True},
        {"report": {"cases": None}},
        {"report": {"cases": []}},
        {"report": {"cases": [{}]}},
        {"report": {"cases": [{"equivalent": False}]}},
        {"report": {"cases": [{"equivalent": "true"}]}},
        {"report": {"cases": [None]}},
        {"report": {"expected_cases": 2}},
        {"report": {"expected_cases": "1"}},
        {"report": {"expected_cases": 0}},
    ],
)
def test_completed_timestamp_cannot_hide_missing_equality_or_an_incomplete_matrix(settings):
    result = publish_comment(previous=True, **settings)
    assert result["failures"]
    body = result["writes"][0]["body"]
    assert "**PARTIAL / FAILED RUN**" in body
    assert "**COMPLETE**" not in body
    assert "Old successful results" not in body


def test_older_complete_artifacts_without_an_expected_count_remain_supported():
    result = publish_comment(missingExpectedCases=True)
    assert not result["failures"]
    assert "**COMPLETE**" in result["writes"][0]["body"]


@pytest.mark.parametrize("settings", [{"staleFirst": True}, {"staleLast": True}, {"closed": True}])
def test_stale_or_closed_pull_requests_cannot_receive_report_updates(settings):
    result = publish_comment(previous=True, **settings)
    assert not result["writes"]
    assert not result["failures"]


@pytest.mark.parametrize("run,attempt", [(201, 1), (200, 3)])
def test_older_workflow_runs_and_attempts_cannot_replace_newer_same_head_results(run, attempt):
    result = publish_comment(
        previous=True,
        previousBody=f"{MARKER}\n<!-- devfeed-feed-options-scale-run:{run}:{attempt} -->",
    )
    assert not result["writes"]


def test_newer_attempt_updates_the_existing_bot_comment():
    result = publish_comment(
        previous=True,
        previousBody=f"{MARKER}\n<!-- devfeed-feed-options-scale-run:200:1 -->",
    )
    assert result["writes"][0]["method"] == "update"
    assert result["writes"][0]["comment_id"] == 900


def test_manual_report_comments_remain_owned_by_their_human_author():
    result = publish_comment(previous=True, author="User")
    assert result["writes"][0]["method"] == "create"
    assert "comment_id" not in result["writes"][0]


def test_publisher_reads_artifacts_without_executing_pr_code_with_write_permissions():
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/feed-options-performance.yml").read_text()
    )
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow_jobs()
    measure, report = jobs["measure"], jobs["report"]
    assert "permissions" not in measure
    assert report["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert report["needs"] == "measure"
    assert "always()" in report["if"]
    assert "!cancelled()" in report["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in report["if"]
    assert report["concurrency"]["cancel-in-progress"] is False
    assert "github.event.pull_request.number" in report["concurrency"]["group"]
    assert all("run" not in step for step in report["steps"])
    assert all("actions/checkout@" not in step["uses"] for step in report["steps"])
    assert [step["uses"].split("@")[0] for step in report["steps"]] == [
        "actions/download-artifact",
        "actions/github-script",
    ]
    download = report["steps"][0]
    assert download["continue-on-error"] is True
    assert download["with"]["name"] == "feed-options-scale-${{ github.run_attempt }}"
