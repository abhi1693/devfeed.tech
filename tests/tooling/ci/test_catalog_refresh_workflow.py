"""Catalog benchmark reporting publishes current evidence without exposing write tokens to code."""

import hashlib
import json
import re
import subprocess
import textwrap
from urllib.parse import urlsplit

import pytest
import yaml

from tests.support.paths import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github/workflows/catalog-refresh-benchmark.yml"
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40


def run_comment(tmp_path, scenario="success"):
    workflow = WORKFLOW.read_text()
    script = textwrap.dedent(
        workflow.split("  report:\n", 1)[1].split("          script: |\n", 1)[1]
    )
    summary = (
        "### Catalog refresh scale\n\n**PASS**\n\n"
        "| Metric | Before | After | Delta |\n| --- | ---: | ---: | ---: |\n"
        "| Published reports | 0/40 | 40/40 | +40 |\n"
        "| Convergence | 341.63 s | 368.32 s | +26.69 s |\n"
    )
    previous_attempt = scenario in {
        "report-rerun",
        "stale-failure",
        "flat-rerun",
        "flat-stale-failure",
        "mismatched-attempt",
    }
    metadata = {
        "schema": 1,
        "run_id": "123",
        "run_attempt": 1 if previous_attempt else 2,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "status": "passed",
        "summary_sha256": hashlib.sha256(summary.encode()).hexdigest(),
    }
    if scenario == "wrong-run":
        metadata["run_id"] = "456"
    elif scenario == "wrong-head":
        metadata["head_sha"] = "c" * 40
    elif scenario == "wrong-base":
        metadata["base_sha"] = "d" * 40
    elif scenario == "future-attempt":
        metadata["run_attempt"] = 3
    elif scenario == "boolean-attempt":
        metadata["run_attempt"] = True
    elif scenario == "wrong-hash":
        metadata["summary_sha256"] = "0" * 64
    elif scenario == "unknown-status":
        metadata["status"] = "@everyone <script>"
    elif scenario == "incomplete":
        metadata["status"] = "incomplete"
        summary = "### Catalog refresh scale\n\n**INCOMPLETE** — phase: continuous.\n"
    elif scenario == "failed":
        metadata["status"] = "failed"
        summary = "### Catalog refresh scale\n\n**FAIL** — publication gate failed.\n"
    elif scenario == "failed-metadata-passing-markdown":
        metadata["status"] = "failed"
    if scenario in {"incomplete", "failed"}:
        metadata["summary_sha256"] = hashlib.sha256(summary.encode()).hexdigest()
    old_artifact = scenario in {"report-rerun", "stale-failure"}
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "script": script,
                "metadata": metadata,
                "summary": summary,
                "result": "failure"
                if scenario in {"job-failed", "stale-failure", "flat-stale-failure"}
                else "success",
                "flat": scenario.startswith("flat-"),
                "artifacts": []
                if scenario == "missing"
                else ["catalog-refresh-arm64-1"]
                if old_artifact
                else [
                    "catalog-refresh-arm64-1",
                    "catalog-refresh-arm64-2",
                    "catalog-refresh-arm64-invalid",
                    "catalog-refresh-arm64-3",
                ],
                "expectedArtifact": "catalog-refresh-arm64-1"
                if scenario == "report-rerun"
                else "catalog-refresh-arm64-2",
                "stale": scenario == "stale",
                "lateStale": scenario == "late-stale",
                "previous": scenario in {"previous", "human", "report-rerun", "flat-rerun"},
                "human": scenario == "human",
                "nonregular": scenario == "nonregular",
                "oversizedMetadata": scenario == "oversized-metadata",
                "oversizedMarkdown": scenario == "oversized-markdown",
                "missingMarkdown": scenario == "missing-markdown",
                "invalidJson": scenario == "invalid-json",
                "head": HEAD_SHA,
                "base": BASE_SHA,
            }
        )
    )
    wrapper = r"""
      const fs = require('fs');
      const vm = require('vm');
      const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
      const calls = [];
      let reads = 0;
      const github = {rest: {
        pulls: {get: async () => ({data: {head: {sha:
          input.stale || (input.lateStale && reads++) ? 'new-head' : input.head}}})},
        issues: {listComments: {}, createComment: async value => calls.push(['create', value]),
          updateComment: async value => calls.push(['update', value])}
      }, paginate: async () => input.previous ? [{id: 7,
        user: {type: input.human ? 'User' : 'Bot'},
        body: '<!-- devfeed-catalog-refresh-scale -->\nold'}] : []};
      const context = {repo: {owner: 'owner', repo: 'repo'},
        serverUrl: 'https://github.com', runId: 123,
        payload: {pull_request: {number: 4,
          head: {sha: input.head}, base: {sha: input.base}}}};
      const data = file => {
        const directory = input.flat ? 'report' : `report/${input.expectedArtifact}`;
        if (file === `${directory}/summary.json`) {
          return Buffer.from(input.invalidJson ? '{broken' : JSON.stringify(input.metadata));
        }
        if (file === `${directory}/summary.md` && !input.missingMarkdown) {
          return Buffer.from(input.summary);
        }
        throw new Error('Wrong or missing artifact file');
      };
      const reportFs = {
        readdirSync: () => input.artifacts,
        existsSync: () => input.flat,
        lstatSync: file => ({isFile: () => !input.nonregular,
          size: file.endsWith('.json') && input.oversizedMetadata ? 10001 :
            file.endsWith('.md') && input.oversizedMarkdown ? 50001 : data(file).length}),
        readFileSync: data
      };
      const sandbox = {require: name => name === 'fs' ? reportFs : require(name),
        github, context,
        core: {setFailed: message => calls.push(['failed', message])},
        process: {env: {MEASUREMENT_RESULT: input.result, GITHUB_RUN_ATTEMPT: '2'}}};
      vm.runInNewContext('(async () => {' + input.script + '})()', sandbox)
        .then(() => console.log(JSON.stringify(calls)))
        .catch(error => {console.error(error); process.exit(1);});
    """
    wrapper_path = tmp_path / "comment.cjs"
    wrapper_path.write_text(textwrap.dedent(wrapper))
    result = subprocess.run(
        ["node", str(wrapper_path), str(input_path)], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "scenario", ["success", "previous", "human", "report-rerun", "flat-single", "flat-rerun"]
)
def test_comment_publishes_signed_results_and_updates_only_the_bot_comment(tmp_path, scenario):
    calls = run_comment(tmp_path, scenario)
    updates_existing = scenario in {"previous", "report-rerun", "flat-rerun"}
    assert len(calls) == 1 and calls[0][0] == ("update" if updates_existing else "create")
    if updates_existing:
        assert calls[0][1]["comment_id"] == 7
    body = calls[0][1]["body"]
    assert "**PASS**" in body and "| Before | After | Delta |" in body
    assert "0/40 | 40/40 | +40" in body and "+26.69 s" in body
    assert HEAD_SHA[:12] in body and BASE_SHA[:12] in body
    assert "actions/runs/123#artifacts" in body and "report attempt 2" in body
    attempt = 1 if scenario in {"report-rerun", "flat-rerun"} else 2
    assert f"measurement attempt {attempt}" in body


@pytest.mark.parametrize(
    "scenario", ["job-failed", "incomplete", "failed", "failed-metadata-passing-markdown"]
)
def test_failed_or_incomplete_measurement_cannot_publish_a_passing_header(tmp_path, scenario):
    calls = run_comment(tmp_path, scenario)
    body = calls[0][1]["body"]
    assert "**PASS**" not in body
    assert ("**INCOMPLETE**" if scenario == "incomplete" else "**FAIL**") in body
    assert calls[1] == ["failed", "Catalog refresh benchmark did not pass"]
    if scenario == "job-failed":
        assert "benchmark job result: failure" in body


@pytest.mark.parametrize(
    "scenario",
    [
        "missing",
        "wrong-run",
        "wrong-head",
        "wrong-base",
        "future-attempt",
        "boolean-attempt",
        "mismatched-attempt",
        "stale-failure",
        "flat-stale-failure",
        "wrong-hash",
        "unknown-status",
        "nonregular",
        "oversized-metadata",
        "oversized-markdown",
        "missing-markdown",
        "invalid-json",
    ],
)
def test_unavailable_or_mismatched_artifact_is_explicit_and_fails_reporting(tmp_path, scenario):
    calls = run_comment(tmp_path, scenario)
    body = calls[0][1]["body"]
    assert "Results unavailable / FAIL" in body and "**PASS**" not in body
    assert "@everyone" not in body and "<script>" not in body
    assert calls[1] == ["failed", "Catalog refresh artifact is missing or invalid"]


@pytest.mark.parametrize("scenario", ["stale", "late-stale"])
def test_superseded_pr_head_receives_no_comment(tmp_path, scenario):
    assert run_comment(tmp_path, scenario) == []


def test_workflow_keeps_writable_reporting_separate_and_measures_candidate_after_baseline_failure():
    workflow = WORKFLOW.read_text()
    native, reporter = workflow.split("  report:\n", 1)
    assert "pull-requests: write" not in native and "pull-requests: write" in reporter
    assert "checkout@" not in reporter and "No checkout" in reporter
    assert "github.event.pull_request.head.repo.full_name == github.repository" in reporter
    assert "github.actor != 'dependabot[bot]'" in reporter
    assert "id: clone-fixture" in native
    assert "if: always() && !cancelled() && steps.clone-fixture.outcome == 'success'" in native
    assert 'exit "$report_status"' in native
    assert native.index('cat reports/summary.md >> "$GITHUB_STEP_SUMMARY"') < native.index(
        'exit "$report_status"'
    )
    assert "--job-result" in native and "BASE_SHA:" in native and "HEAD_SHA:" in native


def test_database_password_is_supplied_by_libpq_for_both_isolated_databases():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["native-scale"]
    assert job["env"]["PGPASSWORD"] == job["services"]["postgres"]["env"]["POSTGRES_PASSWORD"]
    candidate = next(
        step for step in job["steps"] if step.get("name") == "Measure candidate on the same runner"
    )
    candidate_url = re.search(r"DEVFEED_TEST_DATABASE_URL=(\S+)", candidate["run"])[1]
    for url, database in (
        (job["env"]["DEVFEED_TEST_DATABASE_URL"], "devfeed_catalog_base_test"),
        (candidate_url, "devfeed_catalog_head_test"),
    ):
        parsed = urlsplit(url)
        assert parsed.scheme == "postgresql+psycopg"
        assert parsed.username == "postgres" and parsed.password is None
        assert parsed.hostname == "127.0.0.1" and parsed.port == 5432
        assert parsed.path == f"/{database}"


def test_renderer_uses_fixed_report_locations():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["native-scale"]
    summary = next(
        step
        for step in job["steps"]
        if step.get("name") == "Summarize before, after, and signed changes"
    )
    assert "candidate/scripts/ci/catalog_refresh_report.py" in summary["run"]
    assert '--job-result "$MEASUREMENT_RESULT"' in summary["run"]
    for option in ("--reports", "--output", "--metadata"):
        assert option not in summary["run"]
