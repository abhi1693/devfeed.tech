const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const workflow = fs.readFileSync(
  path.join(__dirname, "../.github/workflows/scheduler-recheck-scale.yml"),
  "utf8",
);
const report = workflow.slice(workflow.indexOf("\n  report:\n"));
const scriptBlock = report.match(/\n          script: \|\n([\s\S]+)$/);
if (!scriptBlock) throw new Error("The report job must retain an executable github-script block");
const script = scriptBlock[1]
  .split("\n")
  .map((line) => line.replace(/^ {12}/, ""))
  .join("\n");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction("require", "github", "context", "core", "process", script);
const marker = "<!-- devfeed-scheduler-recheck-scale -->";
const head = "candidate-head";
const base = "comparison-base";
const runId = 12345;

function artifact(articles, attempt = 1, metadata = {}, summary) {
  const directory = `scheduler-rechecks-${articles}-arm64-${attempt}`;
  return {
    directory,
    metadata: {
      PROFILE_HEAD_SHA: head,
      PROFILE_BASE_SHA: base,
      PROFILE_ARTICLES: String(articles),
      GITHUB_RUN_ID: String(runId),
      GITHUB_RUN_ATTEMPT: String(attempt),
      ...metadata,
    },
    summary: summary ?? `#### ${articles} articles\n\nMeasured attempt ${attempt} for ${articles}.`,
  };
}

async function run({
  artifacts = [artifact(100000), artifact(150000)],
  attempt = 1,
  result = "success",
  comments = [],
  pulls = [{ head: { sha: head }, base: { sha: base } }],
} = {}) {
  const writes = [];
  const summaries = [];
  const failures = [];
  let pullIndex = 0;
  const files = new Map();
  for (const item of artifacts) {
    const root = item.flat ? "report" : `report/${item.directory}`;
    if (!item.missingContext) {
      files.set(`${root}/context.json`, item.rawMetadata ?? JSON.stringify(item.metadata));
    }
    if (!item.missingSummary) files.set(`${root}/comment.md`, item.summary);
  }
  const fakeFs = {
    existsSync: (filename) =>
      files.has(filename) || (filename === "report" && artifacts.length > 0),
    readdirSync: () =>
      artifacts.flatMap((item) => (item.flat ? ["context.json", "comment.md"] : [item.directory])),
    readFileSync: (filename, encoding) => {
      assert.equal(encoding, "utf8");
      if (!files.has(filename)) throw new Error(`Missing ${filename}`);
      return files.get(filename);
    },
  };
  const context = {
    repo: { owner: "devfeed", repo: "example" },
    runId,
    serverUrl: "https://github.example",
    payload: {
      pull_request: {
        number: 300,
        user: { login: "author" },
        head: { sha: head },
        base: { sha: base },
      },
    },
  };
  const github = {
    rest: {
      pulls: {
        get: async () => ({ data: pulls[Math.min(pullIndex++, pulls.length - 1)] }),
      },
      issues: {
        listComments: () => {},
        createComment: async (payload) => writes.push({ operation: "create", ...payload }),
        updateComment: async (payload) => writes.push({ operation: "update", ...payload }),
      },
    },
    paginate: async (method, payload) => {
      assert.equal(method, github.rest.issues.listComments);
      assert.equal(payload.issue_number, 300);
      return comments;
    },
  };
  const core = {
    summary: {
      addRaw: (body) => ({ write: async () => summaries.push(body) }),
    },
    setFailed: (message) => failures.push(message),
  };
  await execute(
    (name) => {
      assert.equal(name, "fs");
      return fakeFs;
    },
    github,
    context,
    core,
    { env: { GITHUB_RUN_ATTEMPT: String(attempt), SCALE_RESULT: result } },
  );
  return { writes, summaries, failures, pullIndex };
}

test("creates one PR comment from both validated artifact summaries", async () => {
  const result = await run();
  assert.equal(result.writes.length, 1);
  assert.equal(result.writes[0].operation, "create");
  assert.ok(result.writes[0].body.startsWith(marker));
  assert.match(result.writes[0].body, /\*\*PASS\*\*/);
  assert.match(result.writes[0].body, /Measured attempt 1 for 100000/);
  assert.match(result.writes[0].body, /Measured attempt 1 for 150000/);
  assert.deepEqual(result.failures, []);
  assert.deepEqual(result.summaries, [result.writes[0].body]);
});

test("updates the existing bot comment and selects attempts numerically", async () => {
  const result = await run({
    attempt: 10,
    artifacts: [artifact(100000, 2), artifact(100000, 10), artifact(150000, 10)],
    comments: [
      { id: 1, user: { type: "User" }, body: marker },
      {
        id: 2,
        user: { type: "Bot", login: "github-actions[bot]" },
        body: `${marker}\nPrevious results`,
      },
    ],
  });
  assert.equal(result.writes.length, 1);
  assert.equal(result.writes[0].operation, "update");
  assert.equal(result.writes[0].comment_id, 2);
  assert.match(result.writes[0].body, /Measured attempt 10 for 100000/);
  assert.doesNotMatch(result.writes[0].body, /Measured attempt 2 for 100000/);
  assert.deepEqual(result.failures, []);
});

test("report-only reruns reuse the newest existing successful matrix artifacts", async () => {
  const result = await run({
    attempt: 3,
    artifacts: [artifact(100000, 1), artifact(100000, 2), artifact(150000, 1)],
  });
  assert.match(result.writes[0].body, /Measured attempt 2 for 100000/);
  assert.match(result.writes[0].body, /Measured attempt 1 for 150000/);
  assert.match(result.writes[0].body, /attempt 3/);
  assert.deepEqual(result.failures, []);
});

test("ignores future attempts when selecting artifacts", async () => {
  const result = await run({
    artifacts: [artifact(100000, 1), artifact(100000, 2), artifact(150000, 1)],
  });
  assert.match(result.writes[0].body, /Measured attempt 1 for 100000/);
  assert.doesNotMatch(result.writes[0].body, /Measured attempt 2 for 100000/);
  assert.deepEqual(result.failures, []);
});

test("partial or missing downloads report failure while preserving available results", async () => {
  for (const artifacts of [[artifact(100000)], []]) {
    const result = await run({ artifacts });
    assert.equal(result.writes.length, 1);
    assert.match(result.writes[0].body, /\*\*FAIL \/ incomplete\*\*/);
    assert.match(result.writes[0].body, /Results unavailable/);
    if (artifacts.length) assert.match(result.writes[0].body, /Measured attempt 1 for 100000/);
    assert.equal(result.failures.length, 1);
  }
});

test("a flattened single artifact preserves its valid population and reports the missing one", async () => {
  const result = await run({ artifacts: [{ ...artifact(100000), flat: true }] });
  assert.equal(result.writes.length, 1);
  assert.match(result.writes[0].body, /\*\*FAIL \/ incomplete\*\*/);
  assert.match(result.writes[0].body, /Measured attempt 1 for 100000/);
  assert.match(result.writes[0].body, /#### 150,000 articles\n\n\*\*Results unavailable/);
  assert.doesNotMatch(result.writes[0].body, /Measured attempt 1 for 150000/);
  assert.equal(result.failures.length, 1);
});

test("flattened artifacts still reject invalid attempt and source metadata", async () => {
  for (const metadata of [
    { GITHUB_RUN_ATTEMPT: "2" },
    { GITHUB_RUN_ATTEMPT: "0" },
    { GITHUB_RUN_ATTEMPT: "1.5" },
    { GITHUB_RUN_ATTEMPT: "01" },
    { GITHUB_RUN_ID: "wrong-run" },
    { PROFILE_HEAD_SHA: "wrong-head" },
    { PROFILE_BASE_SHA: "wrong-base" },
  ]) {
    const result = await run({ artifacts: [{ ...artifact(100000, 1, metadata), flat: true }] });
    assert.match(result.writes[0].body, /\*\*FAIL \/ incomplete\*\*/);
    assert.doesNotMatch(result.writes[0].body, /Measured attempt 1 for 100000/);
    assert.equal(result.failures.length, 1);
  }
});

test("rejects the newest invalid artifact without falling back to older success", async () => {
  const result = await run({
    attempt: 2,
    artifacts: [
      artifact(100000, 1),
      artifact(100000, 2, { PROFILE_HEAD_SHA: "wrong-head" }, "UNTRUSTED SUMMARY"),
      artifact(150000, 1),
    ],
  });
  assert.match(result.writes[0].body, /Results unavailable/);
  assert.doesNotMatch(result.writes[0].body, /UNTRUSTED SUMMARY|Measured attempt 1 for 100000/);
  assert.equal(result.failures.length, 1);
});

test("rejects artifacts with mismatched source, run, population, or attempt metadata", async () => {
  for (const metadata of [
    { PROFILE_HEAD_SHA: "wrong-head" },
    { PROFILE_BASE_SHA: "wrong-base" },
    { GITHUB_RUN_ID: "99999" },
    { GITHUB_RUN_ATTEMPT: "2" },
    { PROFILE_ARTICLES: "150000" },
  ]) {
    const result = await run({
      artifacts: [artifact(100000, 1, metadata, "UNTRUSTED SUMMARY"), artifact(150000)],
    });
    assert.doesNotMatch(result.writes[0].body, /UNTRUSTED SUMMARY/);
    assert.equal(result.failures.length, 1);
  }
});

test("rejects oversized and empty artifacts without publishing their content", async () => {
  for (const item of [
    artifact(100000, 1, { padding: "x".repeat(4001) }),
    artifact(100000, 1, {}, "x".repeat(25001)),
    artifact(100000, 1, {}, " \n"),
  ]) {
    const result = await run({ artifacts: [item, artifact(150000)] });
    assert.match(result.writes[0].body, /Results unavailable/);
    assert.equal(result.failures.length, 1);
  }
});

test("incomplete or malformed artifact contents cannot produce a passing report", async () => {
  for (const item of [
    { ...artifact(100000), missingContext: true },
    { ...artifact(100000), missingSummary: true },
    { ...artifact(100000), rawMetadata: "{incomplete json" },
  ]) {
    const result = await run({ artifacts: [item, artifact(150000)] });
    assert.match(result.writes[0].body, /\*\*FAIL \/ incomplete\*\*/);
    assert.match(result.writes[0].body, /Results unavailable/);
    assert.match(result.writes[0].body, /Measured attempt 1 for 150000/);
    assert.doesNotMatch(result.writes[0].body, /Measured attempt 1 for 100000/);
    assert.equal(result.failures.length, 1);
  }
});

test("failed comparison jobs retain their failure banner even with complete artifacts", async () => {
  const result = await run({ result: "failure" });
  assert.match(result.writes[0].body, /\*\*FAIL \/ incomplete\*\*/);
  assert.match(result.writes[0].body, /Measured attempt 1 for 100000/);
});

for (const field of ["head", "base"]) {
  test(`a changed PR ${field} prevents publishing a stale report`, async () => {
    const result = await run({
      pulls: [{ head: { sha: head }, base: { sha: base }, [field]: { sha: "changed" } }],
    });
    assert.deepEqual(result.writes, []);
    assert.deepEqual(result.summaries, []);
  });

  test(`a PR ${field} change during report assembly prevents the final write`, async () => {
    const result = await run({
      pulls: [
        { head: { sha: head }, base: { sha: base } },
        { head: { sha: head }, base: { sha: base }, [field]: { sha: "changed" } },
      ],
    });
    assert.equal(result.pullIndex, 2);
    assert.deepEqual(result.writes, []);
  });
}

test("adopts the original author's results comment instead of adding another", async () => {
  const result = await run({
    comments: [
      { id: 12, user: { login: "github-actions[bot]" }, body: `${marker}\nLater bot report` },
      {
        id: 10,
        user: { login: "author" },
        body: "<!-- devfeed-scheduler-recheck-evidence-123 -->\nOriginal report",
      },
    ],
  });
  assert.equal(result.writes.length, 1);
  assert.equal(result.writes[0].operation, "update");
  assert.equal(result.writes[0].comment_id, 10);
  assert.ok(result.writes[0].body.startsWith(marker));
});

test("updates an author-created report after its stable marker has been adopted", async () => {
  const result = await run({ comments: [{ id: 10, user: { login: "author" }, body: marker }] });
  assert.equal(result.writes[0].operation, "update");
  assert.equal(result.writes[0].comment_id, 10);
});

test("does not adopt matching comments from other users or unrelated bots", async () => {
  const result = await run({
    comments: [
      { id: 8, user: { login: "stranger" }, body: marker },
      {
        id: 9,
        user: { login: "another[bot]" },
        body: "<!-- devfeed-scheduler-recheck-evidence-123 -->",
      },
    ],
  });
  assert.equal(result.writes[0].operation, "create");
});
