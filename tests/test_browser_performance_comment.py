"""Exercise the real workflow formatter before the sticky comment action publishes it."""

import json
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = (
    "# Browser budgets\n\n**⚠️ 1 target warnings** · Bold values need attention.\n\n"
    "| Page | FCP | LCP | CLS | TBT | JS | Total |\n"
    "| Latest (`/latest`) | 1.10 s | **3.25 s** | 0.000 | 202 ms | 267 KiB | 879 KiB |\n"
    "| Article preview (`/articles/…`) | 1.11 s | 2.90 s | 0.000 | 150 ms | 272 KiB | 892 KiB |\n"
    "| Topics (`/topics`) | 1.10 s | 1.25 s | 0.000 | 100 ms | 260 KiB | 820 KiB |\n"
    "| Sources (`/sources`) | 1.10 s | 1.25 s | 0.000 | 100 ms | 260 KiB | 820 KiB |\n"
    "| Public profile (`/users/…`) | 1.10 s | 1.25 s | 0.000 | 100 ms | 260 KiB | 820 KiB |\n"
    "| Leaderboard (`/leaderboard`) | 1.10 s | 1.25 s | 0.000 | 100 ms | 260 KiB | 820 KiB |\n"
    "\n**Needs attention**\n\n"
    "| Page | Finding | Measured | Target / limit |\n"
    "| Latest (`/latest`) | ⚠️ Target · LCP | 3.25 s | 2.50 s |\n"
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
    assert "Lighthouse · ⚠️ Targets exceeded" in body
    assert SUMMARY.removeprefix("# Browser budgets\n\n") in body
    assert "https://github.com/abhi1693/devfeed.tech/actions/runs/123" in body
    assert "Attempt 2" in body
    assert "[aaaaaaa](https://github.com/abhi1693/devfeed.tech/commit/" + "a" * 40 in body
    assert "<details>" not in body
    assert body.count("| Page | FCP |") == 1
    assert "# Browser budgets" not in body


def test_browser_comment_marks_results_without_warnings_as_targets_met():
    result = format_comment(
        summary=SUMMARY.replace("**⚠️ 1 target warnings** · Bold values need attention.\n\n", "")
    )
    assert not result["failures"]
    assert "Lighthouse · ✅ Targets met" in result["files"]["browser-comment.md"]


def test_browser_comment_preserves_partial_results_for_failed_measurements():
    summary = "# Browser budgets\n\nReader browser budgets failed: Missing measurements\n"
    result = format_comment(status="failure", summary=summary)
    assert "Lighthouse · ❌ Measurement failed" in result["files"]["browser-comment.md"]
    assert summary.removeprefix("# Browser budgets\n\n") in result["files"]["browser-comment.md"]


@pytest.mark.parametrize(
    "settings",
    [
        {"missing": True},
        {"symlink": True},
        {"size": 0},
        {"size": 50001},
        {"summary": "Malformed report"},
        {"summary": "# Browser budgets\n\n| Latest (`/latest`) |\n"},
    ],
)
def test_browser_comment_missing_or_invalid_results_fail_closed(settings):
    result = format_comment(**settings)
    assert result["failures"] == ["Browser report is missing or invalid"]
    body = result["files"]["browser-comment.md"]
    assert "Lighthouse · ❌ Measurement failed" in body
    assert "Results unavailable" in body
    assert "Sensitive internal detail" not in body


def test_browser_comment_cannot_overwrite_results_for_a_newer_commit():
    result = format_comment(stale=True, missing=True)
    assert result == {"outputs": {}, "failures": [], "files": {}}


@pytest.mark.parametrize(
    "name", ["Latest", "Article preview", "Topics", "Sources", "Public profile", "Leaderboard"]
)
def test_browser_comment_rejects_a_success_report_missing_any_required_page(name):
    summary = "\n".join(line for line in SUMMARY.splitlines() if not line.startswith(f"| {name} ("))
    result = format_comment(summary=summary)
    assert result["failures"] == ["Browser report is missing or invalid"]
    assert "❌ Measurement failed" in result["files"]["browser-comment.md"]


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


def test_lighthouse_waits_for_tests_and_does_not_hide_their_failures():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["browser-budgets"]
    tests = {
        "python-unit",
        "python-integration",
        "admin-web",
        "user-web",
        "extensions",
        "reader-parity",
        "admin-browser",
        "live-browser",
        "api-fuzz",
        "mutation",
        "property",
        "recovery",
    }
    assert set(job["needs"]) == tests
    for name in tests:
        assert f"needs.{name}.result == 'success'" in job["if"]
    assert "github.event_name == 'push' && needs.api-fuzz.result == 'skipped'" in job["if"]
    assert "continue-on-error" not in job
    assert workflow["jobs"]["browser-report"]["needs"] == "browser-budgets"


def test_browser_comment_replaces_stale_metrics_when_tests_prevent_measurement():
    result = format_comment(status="skipped", missing=True)
    assert not result["failures"]
    assert result["outputs"] == {"current": "true"}
    body = result["files"]["browser-comment.md"]
    assert "Lighthouse · ⏸️ Not measured" in body
    assert "Tests did not pass for this commit." in body
    assert "| Page | FCP |" not in body
    assert "a" * 40 in body
    assert format_comment(status="skipped", stale=True, missing=True) == {
        "outputs": {},
        "failures": [],
        "files": {},
    }
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("  browser-report:\n", 1)[1].split("  performance:\n", 1)[0]
    assert "needs.browser-budgets.result == 'skipped'" in job
    assert "if: needs.browser-budgets.result != 'skipped'" in job
    assert "only_update: ${{ needs.browser-budgets.result == 'skipped' }}" in job
    required = workflow.split("  required:\n", 1)[1]
    condition = required.split("BROWSER_COMMENT_REQUIRED:", 1)[1].splitlines()[0]
    assert "needs.browser-budgets.result == 'skipped'" in condition
