"""Exercise the real workflow formatter before the sticky comment action publishes it."""

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = (
    "# Browser budgets\n\n### Reader (mobile)\n"
    "| Page | FCP | LCP | CLS | TBT | JS | Total |\n"
    "| /latest | 1.10 s | 3.25 s | 0.000 | 202 ms | 267 KiB | 879 KiB |\n"
    "- warn: /latest / largest-contentful-paint: 3250 (limit 2500)\n"
    "### Admin (desktop)\n| / | 0.25 s | 0.73 s | 0.000 | 0 ms | 405 KiB | 494 KiB |\n"
)


def format_comment(**settings):
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    report = workflow.split("  browser-report:\n", 1)[1].split("  performance:\n", 1)[0]
    source = textwrap.dedent(
        report.split("          script: |\n", 1)[1].split("      - name:", 1)[0]
    )
    harness = r"""
const assert = require('node:assert/strict');
const input = JSON.parse(process.argv[1]);
const result = {outputs: {}, failures: [], files: {}};
const sha = 'a'.repeat(40);
const context = {
  payload: {pull_request: {number: 92, head: {sha}}},
  repo: {owner: 'abhi1693', repo: 'devfeed.tech'},
  serverUrl: 'https://github.com', runId: 123,
};
const fs = {
  lstatSync(file) {
    assert.equal(file, 'report/summary.md');
    if (input.missing) throw new Error('Sensitive internal detail');
    return {isFile: () => !input.symlink, size: input.size ?? Buffer.byteLength(input.summary)};
  },
  readFileSync(file, encoding) {
    assert.equal(file, 'report/summary.md'); assert.equal(encoding, 'utf8');
    return input.summary;
  },
  writeFileSync(file, body) {
    assert.equal(file, 'browser-comment.md'); result.files[file] = body;
  },
};
const core = {
  setOutput: (key, value) => { result.outputs[key] = value; },
  setFailed: (message) => { result.failures.push(message); },
};
const github = {rest: {pulls: {get: async request => {
  assert.equal(request.pull_number, 92);
  return {data: {head: {sha: input.stale ? 'b'.repeat(40) : sha}}};
}}}};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
new AsyncFunction('require', 'context', 'github', 'core', 'process', input.source)(
  name => { assert.equal(name, 'fs'); return fs; }, context, github, core,
  {env: {BUDGET_RESULT: input.status, GITHUB_RUN_ATTEMPT: '2'}},
).then(() => process.stdout.write(JSON.stringify(result)));
"""
    completed = subprocess.run(
        [
            "node",
            "-e",
            harness,
            json.dumps({"source": source, "summary": SUMMARY, "status": "success", **settings}),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(completed.stdout)


def test_browser_comment_keeps_measured_metrics_warnings_and_artifact_link():
    result = format_comment()
    assert not result["failures"]
    assert result["outputs"] == {"current": "true"}
    body = result["files"]["browser-comment.md"]
    assert "Lighthouse budgets: PASS" in body
    assert SUMMARY in body
    assert "https://github.com/abhi1693/devfeed.tech/actions/runs/123" in body
    assert "attempt 2" in body
    assert "Commit: " + "a" * 40 in body


def test_browser_comment_preserves_partial_results_for_failed_measurements():
    summary = (
        "# Browser budgets\n\n### Reader (mobile)\n"
        "web browser budgets failed: Missing measurements\n"
    )
    result = format_comment(status="failure", summary=summary)
    assert "Lighthouse budgets: FAIL" in result["files"]["browser-comment.md"]
    assert summary in result["files"]["browser-comment.md"]


@pytest.mark.parametrize(
    "settings",
    [
        {"missing": True},
        {"symlink": True},
        {"size": 0},
        {"size": 50001},
        {"summary": "Malformed report"},
        {"summary": "# Browser budgets\n\n### Reader (mobile)\n"},
    ],
)
def test_browser_comment_missing_or_invalid_results_fail_closed(settings):
    result = format_comment(**settings)
    assert result["failures"] == ["Browser report is missing or invalid"]
    body = result["files"]["browser-comment.md"]
    assert "Lighthouse budgets: FAIL" in body
    assert "Results unavailable" in body
    assert "Sensitive internal detail" not in body


def test_browser_comment_cannot_overwrite_results_for_a_newer_commit():
    result = format_comment(stale=True, missing=True)
    assert result == {"outputs": {}, "failures": [], "files": {}}


def test_browser_comment_uses_pinned_sticky_action_without_executing_pr_code():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("  browser-report:\n", 1)[1].split("  performance:\n", 1)[0]
    assert "actions/checkout@" not in job
    assert "github.event.pull_request.head.repo.full_name == github.repository" in job
    assert "github.actor != 'dependabot[bot]'" in job
    assert "steps.prepare.outputs.current == 'true'" in job
    assert "marocchino/sticky-pull-request-comment@5770ad5eb8f42dd2c4f34da00c94c5381e49af88" in job
    assert "header: browser-budgets\n" in job
    assert "path: browser-comment.md\n" in job
