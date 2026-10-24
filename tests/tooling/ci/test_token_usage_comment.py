"""Run the workflow's actual publisher against mocked GitHub and artifact access."""

import json
import subprocess

import pytest
import yaml

from tests.support.paths import REPO_ROOT

MARKER = "<!-- devfeed-token-usage-performance -->"
SUMMARY = (
    "**PASS — Token usage benchmark:** all checks passed.\n\n"
    "| Window | Before (ms) | After (ms) | Improvement (+/−) |\n"
    "| 30d | 100.00 | 75.00 | +25.0% |\n"
    "| 1d | 10.00 | 11.00 | -10.0% |\n"
)


def comment(identifier=12, *, run=122, attempt=1, login="github-actions[bot]", kind="Bot"):
    return {
        "id": identifier,
        "user": {"login": login, "type": kind},
        "body": f"{MARKER}\n<!-- devfeed-token-usage-run:{run} attempt:{attempt} -->\nOld results",
    }


def publish(**settings):
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/token-usage-performance.yml").read_text()
    )
    source = next(
        step["with"]["script"]
        for step in workflow["jobs"]["report"]["steps"]
        if step.get("uses", "").startswith("actions/github-script@")
    )
    harness = r"""
const assert = require('node:assert/strict');
const input = JSON.parse(process.argv[1]);
const result = {created: [], updated: [], failures: [], reads: 0, pullChecks: 0};
const sha = 'a'.repeat(40);
const context = {
  payload: {pull_request: {number: 301, head: {sha}}},
  repo: {owner: 'abhi1693', repo: 'devfeed.tech'},
  serverUrl: 'https://github.com', runId: 123,
};
globalThis.artifactExecuted = false;
const fs = {
  lstatSync(file) {
    assert.equal(file, 'report/summary.md');
    if (input.missing) throw new Error('Private error details');
    return {isFile: () => !input.symlink, size: input.size ?? Buffer.byteLength(input.summary)};
  },
  readFileSync(file, encoding) {
    assert.equal(file, 'report/summary.md'); assert.equal(encoding, 'utf8');
    result.reads += 1;
    return input.summary;
  },
};
const core = {
  info: () => {},
  setFailed: message => result.failures.push(message),
};
const listComments = () => {throw new Error('Pagination must be mocked');};
const github = {
  rest: {
    pulls: {get: async request => {
      assert.equal(request.pull_number, 301);
      result.pullChecks += 1;
      const stale = input.stale || (input.headChanged && result.pullChecks > 1);
      return {data: {head: {sha: stale ? 'b'.repeat(40) : sha}}};
    }},
    issues: {
      listComments,
      createComment: async request => result.created.push(request),
      updateComment: async request => result.updated.push(request),
    },
  },
  paginate: async (method, request) => {
    assert.equal(method, listComments); assert.equal(request.issue_number, 301);
    return input.comments ?? [];
  },
};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
new AsyncFunction('require', 'context', 'github', 'core', 'process', input.source)(
  name => {assert.equal(name, 'fs'); return fs;}, context, github, core,
  {env: {
    BENCHMARK_RESULT: input.status, DOWNLOAD_OUTCOME: input.download, GITHUB_RUN_ATTEMPT: '2',
  }},
).then(() => {
  result.artifactExecuted = globalThis.artifactExecuted;
  process.stdout.write(JSON.stringify(result));
}).catch(error => {console.error(error); process.exitCode = 1;});
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
                    "download": "success",
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


def test_publisher_creates_comment_with_signed_results_and_run_link():
    result = publish()
    assert not result["failures"]
    assert not result["updated"]
    assert len(result["created"]) == 1
    request = result["created"][0]
    assert request["issue_number"] == 301
    assert request["owner"] == "abhi1693"
    assert request["repo"] == "devfeed.tech"
    assert request["body"].startswith(MARKER)
    assert SUMMARY in request["body"]
    assert "https://github.com/abhi1693/devfeed.tech/actions/runs/123" in request["body"]
    assert "attempt 2" in request["body"]
    assert not result["artifactExecuted"]


def test_publisher_updates_only_latest_matching_actions_bot_comment():
    result = publish(
        comments=[
            comment(30, login="octocat", kind="User"),
            comment(29, login="renovate[bot]"),
            comment(12),
            comment(11),
        ]
    )
    assert not result["created"]
    assert len(result["updated"]) == 1
    assert result["updated"][0]["comment_id"] == 12
    assert SUMMARY in result["updated"][0]["body"]


@pytest.mark.parametrize(
    "settings",
    [
        {"login": "octocat", "kind": "User"},
        {"login": "github-actions[bot]", "kind": "User"},
        {"login": "another[bot]", "kind": "Bot"},
    ],
)
def test_publisher_does_not_overwrite_human_or_other_bot_comments(settings):
    result = publish(comments=[comment(**settings)])
    assert len(result["created"]) == 1
    assert not result["updated"]


@pytest.mark.parametrize("settings", [{"stale": True}, {"headChanged": True}])
def test_publisher_does_not_publish_results_after_pr_head_changes(settings):
    result = publish(**settings)
    assert not result["created"]
    assert not result["updated"]
    assert not result["failures"]
    if settings.get("stale"):
        assert result["reads"] == 0
    else:
        assert result["pullChecks"] == 2


@pytest.mark.parametrize(
    "previous", [comment(run=124), comment(run=123, attempt=3), comment(run=9007199254740993)]
)
def test_older_run_or_attempt_cannot_replace_newer_results(previous):
    result = publish(comments=[previous])
    assert not result["created"]
    assert not result["updated"]


@pytest.mark.parametrize(
    "settings",
    [
        {"missing": True},
        {"download": "failure"},
        {"symlink": True},
        {"size": 50001},
        {"summary": "Malformed artifact"},
    ],
)
def test_missing_or_invalid_artifact_publishes_incomplete_diagnostics_and_fails(settings):
    result = publish(**settings)
    body = result["created"][0]["body"]
    assert "**INCOMPLETE — Token usage benchmark:**" in body
    assert "no readable results artifact" in body
    assert result["failures"]
    assert "Private error details" not in body
    assert "**PASS" not in body


@pytest.mark.parametrize(
    "status,verdict", [("failure", "FAIL"), ("cancelled", "INCOMPLETE"), ("skipped", "INCOMPLETE")]
)
def test_failed_benchmark_cannot_retain_pass_from_partial_artifact(status, verdict):
    result = publish(status=status)
    body = result["created"][0]["body"]
    assert f"**{verdict} — Token usage benchmark:**" in body
    assert "**PASS" not in body
    assert "| 30d | 100.00 | 75.00 | +25.0% |" in body
    assert "| 1d | 10.00 | 11.00 | -10.0% |" in body
    assert f"Benchmark job outcome: **{status}**" in body


def test_artifact_is_published_as_inert_text_without_executing_its_contents():
    payload = (
        "${globalThis.artifactExecuted = true}\n<script>globalThis.artifactExecuted = true</script>"
    )
    result = publish(summary=SUMMARY + payload)
    assert not result["artifactExecuted"]
    assert payload in result["created"][0]["body"]
    assert not result["failures"]
