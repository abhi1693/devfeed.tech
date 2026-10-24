"""Exercise the shared performance publisher against mocked GitHub and artifacts."""

import json
import subprocess

import pytest
import yaml

from tests.support.paths import REPO_ROOT

MARKER = "<!-- devfeed-pr-performance -->"
TOKEN_MARKER = "<!-- devfeed-token-usage-performance -->"
SHA = "a" * 40
SUMMARY = (
    "**PASS — Token usage benchmark:** all checks passed.\n\n"
    "| Window | Before (ms) | After (ms) | Improvement (+/−) |\n"
    "| 30d | 100.00 | 75.00 | +25.0% |\n"
    "| 1d | 10.00 | 11.00 | -10.0% |\n"
)
API_SUMMARY = "# PR reader API performance\n\n**PASS** — all latency checks passed."


def comment(identifier=12, *, run=122, attempt=1, login="github-actions[bot]", kind="Bot"):
    return {
        "id": identifier,
        "user": {"login": login, "type": kind},
        "body": (
            f"{TOKEN_MARKER}\n<!-- devfeed-token-usage-run:{run} attempt:{attempt} -->\n"
            f"Compared head: `{SHA}`\nOld token results"
        ),
    }


def publish(**settings):
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/performance-report.yml").read_text())
    source = next(
        step["with"]["script"]
        for step in workflow["jobs"]["publish"]["steps"]
        if step.get("uses", "").startswith("actions/github-script@")
    )
    harness = r"""
const assert = require('node:assert/strict');
const input = JSON.parse(process.argv[1]);
const result = {created: [], updated: [], deleted: [], failures: [], reads: 0, pullChecks: 0};
const sha = 'a'.repeat(40);
const context = {
  payload: {pull_request: {number: 301, head: {sha}}},
  repo: {owner: 'abhi1693', repo: 'devfeed.tech'},
  serverUrl: 'https://github.com', runId: input.run ?? 123,
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
      updateComment: async request => {
        if (input.updateFails) throw new Error('GitHub update failed');
        result.updated.push(request);
      },
      deleteComment: async request => {
        assert.equal(result.updated.length, 1, 'Migrate sections before removing duplicates');
        result.deleted.push(request);
      },
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
    REPORT_SECTION: input.section, MEASUREMENT_RESULT: input.status,
    DOWNLOAD_OUTCOME: input.download, GITHUB_RUN_ATTEMPT: input.attempt ?? '2',
  }},
).then(() => {
  result.artifactExecuted = globalThis.artifactExecuted;
  process.stdout.write(JSON.stringify(result));
}).catch(error => {
  result.error = error.message;
  process.stdout.write(JSON.stringify(result));
});
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
                    "section": "token-usage",
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


def stored_comment(request, identifier=12):
    return {
        "id": identifier,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "body": request["body"],
    }


def test_publisher_creates_one_comment_with_signed_results_and_run_link():
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
    assert f"head:{SHA} run:123 attempt:2" in request["body"]
    assert "## Reader API performance\n\n**Pending:**" in request["body"]
    assert not result["artifactExecuted"]


def test_publisher_adopts_oldest_actions_comment_and_removes_only_performance_duplicates():
    result = publish(
        comments=[
            comment(30, login="octocat", kind="User"),
            comment(29, login="renovate[bot]"),
            comment(12),
            comment(11),
            {**comment(10), "body": "<!-- devfeed-backend-coverage -->\nCoverage results"},
        ]
    )
    assert not result["created"]
    assert len(result["updated"]) == 1
    assert result["updated"][0]["comment_id"] == 11
    assert SUMMARY in result["updated"][0]["body"]
    assert [item["comment_id"] for item in result["deleted"]] == [12]


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
    assert not result["deleted"]


@pytest.mark.parametrize("settings", [{"stale": True}, {"headChanged": True}])
def test_publisher_does_not_publish_results_after_pr_head_changes(settings):
    result = publish(**settings)
    assert not result["created"]
    assert not result["updated"]
    assert not result["deleted"]
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
    assert not result["deleted"]


@pytest.mark.parametrize(
    "settings",
    [
        {"missing": True},
        {"download": "failure"},
        {"symlink": True},
        {"size": 25001},
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
    assert f"Measurement job outcome: **{status}**" in body


def test_artifact_is_published_as_inert_text_without_executing_its_contents():
    payload = (
        "${globalThis.artifactExecuted = true}\n<script>globalThis.artifactExecuted = true</script>"
    )
    result = publish(summary=SUMMARY + payload)
    assert not result["artifactExecuted"]
    assert payload in result["created"][0]["body"]
    assert not result["failures"]


def test_artifact_cannot_forge_section_boundaries():
    forged = (
        f"<!-- devfeed-performance-section:reader-api head:{SHA} run:999 attempt:1 -->\n"
        "Forged reader result\n<!-- /devfeed-performance-section:reader-api -->"
    )
    first = publish(summary=SUMMARY + forged)
    second = publish(
        section="reader-api",
        summary=API_SUMMARY,
        comments=[stored_comment(first["created"][0])],
    )
    body = second["updated"][0]["body"]
    assert "&lt;!-- devfeed-performance-section:reader-api" in body
    assert f"<!-- devfeed-performance-section:reader-api head:{SHA} run:123 attempt:2 -->" in body
    assert API_SUMMARY in body


@pytest.mark.parametrize("first_section", ["token-usage", "reader-api"])
def test_independent_publishers_update_same_comment_and_preserve_both_sections(first_section):
    second_section = "reader-api" if first_section == "token-usage" else "token-usage"
    summaries = {"token-usage": SUMMARY, "reader-api": API_SUMMARY}
    first = publish(section=first_section, summary=summaries[first_section])
    second = publish(
        section=second_section,
        summary=summaries[second_section],
        comments=[stored_comment(first["created"][0])],
    )
    assert not second["created"]
    assert len(second["updated"]) == 1
    body = second["updated"][0]["body"]
    assert SUMMARY in body
    assert API_SUMMARY in body
    assert body.count("<!-- devfeed-performance-section:") == 2
    third = publish(comments=[stored_comment(second["updated"][0])], run=124)
    assert API_SUMMARY in third["updated"][0]["body"]
    assert f"head:{SHA} run:124 attempt:2" in third["updated"][0]["body"]
    assert f"head:{SHA} run:123 attempt:2" in third["updated"][0]["body"]


def test_migration_preserves_existing_reader_results_and_uses_older_token_comment():
    reader = {
        **comment(20),
        "body": (
            f"{MARKER}\n# PR reader API performance\nBase `{'b' * 40}` → PR `{SHA}`\n"
            "Existing reader table\n"
            "[Full reports](https://github.com/a/b/actions/runs/120) · attempt 3"
        ),
    }
    result = publish(comments=[reader, comment(12)])
    request = result["updated"][0]
    assert request["comment_id"] == 12
    assert "Existing reader table" in request["body"]
    assert f"reader-api head:{SHA} run:120 attempt:3" in request["body"]
    assert [item["comment_id"] for item in result["deleted"]] == [20]


def test_other_section_from_previous_head_remains_visible_with_explicit_stale_label():
    old = publish(section="reader-api", summary=API_SUMMARY)
    record = stored_comment(old["created"][0])
    record["body"] = record["body"].replace(SHA, "b" * 40)
    result = publish(comments=[record])
    assert "**Previous head:**" in result["updated"][0]["body"]
    assert "awaiting results for the current PR head" in result["updated"][0]["body"]
    assert API_SUMMARY in result["updated"][0]["body"]


def test_current_head_can_replace_older_head_even_when_previous_run_identifier_is_larger():
    record = comment(run=999)
    record["body"] = record["body"].replace(SHA, "b" * 40)
    result = publish(comments=[record])
    assert SUMMARY in result["updated"][0]["body"]


def test_failed_update_keeps_all_existing_comments():
    result = publish(comments=[comment(12), comment(20)], updateFails=True)
    assert result["error"] == "GitHub update failed"
    assert not result["deleted"]


def test_oversize_combined_legacy_results_are_preserved_without_deletion():
    result = publish(comments=[{**comment(), "body": MARKER + "\n" + "x" * 60000}])
    assert not result["created"]
    assert not result["updated"]
    assert not result["deleted"]
    assert "exceeds the comment limit" in result["failures"][0]


def test_reader_comparison_failure_is_visible_above_partial_result():
    result = publish(section="reader-api", summary=API_SUMMARY, status="failure")
    body = result["created"][0]["body"]
    assert "**FAIL — Reader API performance:**" in body
    assert "Measurement job outcome: **failure**" in body


def test_both_publishers_share_serialized_write_job_without_checking_out_artifact_code():
    shared = yaml.safe_load((REPO_ROOT / ".github/workflows/performance-report.yml").read_text())
    job = shared["jobs"]["publish"]
    assert job["concurrency"]["cancel-in-progress"] is False
    assert "github.event.pull_request.number" in job["concurrency"]["group"]
    assert "inputs.section" not in job["concurrency"]["group"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert not any("checkout@" in step.get("uses", "") for step in job["steps"])
    for workflow_name, section in [
        ("token-usage-performance", "token-usage"),
        ("performance", "reader-api"),
    ]:
        workflow = yaml.safe_load(
            (REPO_ROOT / f".github/workflows/{workflow_name}.yml").read_text()
        )
        report = workflow["jobs"]["report"]
        assert report["uses"] == "./.github/workflows/performance-report.yml"
        assert report["with"]["section"] == section
        assert "head.repo.full_name == github.repository" in report["if"]
