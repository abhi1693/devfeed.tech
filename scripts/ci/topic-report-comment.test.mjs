import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { posix as path } from "node:path";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const marker = "<!--devfeed-topic-report-history-->";
const sha = "abcdef0123456789abcdef0123456789abcdef01";
const attempts = [100000, 300000];

function artifact(articles) {
  const legacy = {
    median_report_pair_ms: 100,
    application_process_cpu_seconds: 1,
    report_plan_totals: {
      execution_ms: 1000,
      shared_hit_blocks: 100,
      shared_read_blocks: 20,
      temp_read_blocks: 12,
      temp_written_blocks: 10,
    },
    report_pairs: 12,
    report_workers: 4,
    days: 90,
    writer_phase_overlap_pairs: 12,
    ingestion: { median_ms: 4, p95_ms: 8, iterations: 200, committed: false },
  };
  const facts = structuredClone(legacy);
  facts.median_report_pair_ms = 50;
  facts.application_process_cpu_seconds = 0.75;
  facts.report_plan_totals.execution_ms = 250;
  facts.report_plan_totals.temp_read_blocks = 0;
  facts.report_plan_totals.temp_written_blocks = 0;
  facts.ingestion.median_ms = 5;
  facts.ingestion.p95_ms = 10;
  return {
    complete: true,
    client_architecture: "aarch64",
    server: {
      version: "PostgreSQL 17.11 on aarch64-unknown-linux-gnu",
      version_num: "170011",
      default_work_mem: "4MB",
    },
    fixture: {
      articles,
      runs: articles * 0.06,
      calls: articles * 0.18,
      inference_calls: articles * 5,
    },
    reports: [1, 7, 30, 90].map((days) => ({
      days,
      exact_output_parity: true,
      strategies: {
        legacy: [{ elapsed_ms: 100 }, { elapsed_ms: 100 }, { elapsed_ms: 100 }],
        facts: [{ elapsed_ms: 50 }, { elapsed_ms: 50 }, { elapsed_ms: 50 }],
      },
    })),
    concurrent: { legacy, facts },
    correction_plans: {
      legacy: { statement_wal_bytes: 1000, "Execution Time": 0.5, Plan: { "WAL FPI": 0 } },
      facts: { statement_wal_bytes: 1500, "Execution Time": 0.75, Plan: { "WAL FPI": 0 } },
    },
    backfill_equivalent_plan: { "Execution Time": 2000 },
    improvement: {
      report_runtime_lower: true,
      topic_sql_runtime_lower: true,
      topic_sql_zero_temp_writes: true,
      all_reports_overlap_writer_phase: true,
    },
  };
}

function committedProfiles({ slower = true } = {}) {
  return [5, 20].map((calls) => {
    const legacy = {
      iterations: 600,
      committed_transactions: 2400,
      committed: true,
      median_ms: 1,
      p95_ms: 2,
      elapsed_seconds: 3,
      application_thread_cpu_seconds: 1,
      wal_bytes: 1024,
      wal_records: 20,
      wal_full_page_images: 1,
      operations: Object.fromEntries(
        ["append", "settle", "correction", "checkpoint"].map((operation) => [
          operation,
          { samples: 600, median_ms: 1, p95_ms: 2 },
        ]),
      ),
    };
    const facts = structuredClone(legacy);
    facts.operations.append.median_ms = slower ? 1.5 : 0.5;
    facts.elapsed_seconds = slower ? 4.5 : 2.5;
    facts.application_thread_cpu_seconds = slower ? 1.5 : 0.75;
    facts.wal_bytes = 2048;
    facts.wal_full_page_images = 2;
    return {
      calls_per_workflow: calls,
      repeats: 3,
      paired_order: [
        ["legacy", "facts"],
        ["facts", "legacy"],
        ["legacy", "facts"],
      ],
      source_states_equal: true,
      projection_verified: true,
      strategies: { legacy, facts },
    };
  });
}

function changeArtifacts(fixture, change) {
  for (const [file, raw] of fixture.files) {
    const data = JSON.parse(raw);
    change(data);
    fixture.files.set(file, JSON.stringify(data));
  }
}

function script() {
  const workflow = readFileSync(
    new URL("../../.github/workflows/topic-report-profile.yml", import.meta.url),
    "utf8",
  );
  const begin = "// BEGIN TOPIC REPORT COMMENT";
  const end = "// END TOPIC REPORT COMMENT";
  assert.equal(
    workflow.split(begin).length,
    2,
    "The actual workflow script needs one start marker",
  );
  assert.equal(workflow.split(end).length, 2, "The actual workflow script needs one end marker");
  const code = workflow
    .split(begin)[1]
    .split(end)[0]
    .split("\n")
    .map((line) => line.replace(/^ {12}/, ""))
    .join("\n");
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  return new AsyncFunction("github", "context", "core", "require", "process", code);
}

function harness({ attempt = "2", heads = [sha], fork = false, comments = [] } = {}) {
  const files = new Map(
    attempts.map((articles) => [
      `report/topic-reports-${articles}-arm64-${attempt}/topic-reports.json`,
      JSON.stringify(artifact(articles)),
    ]),
  );
  const calls = {
    reads: [],
    writes: [],
    gets: [],
    lists: [],
    creates: [],
    updates: [],
    failures: [],
  };
  const summary = [];
  const fs = {
    existsSync: (file) => files.has(path.normalize(String(file))),
    statSync(file) {
      const target = path.normalize(String(file));
      if (!files.has(target))
        throw Object.assign(new Error(`Missing ${target}`), { code: "ENOENT" });
      return { size: Buffer.byteLength(files.get(target)) };
    },
    readFileSync(file, encoding) {
      const target = path.normalize(String(file));
      calls.reads.push(target);
      if (!files.has(target))
        throw Object.assign(new Error(`Missing ${target}`), { code: "ENOENT" });
      return encoding ? files.get(target) : Buffer.from(files.get(target));
    },
    mkdirSync() {},
    writeFileSync(file, value) {
      const target = path.normalize(String(file));
      calls.writes.push({ file: target, value: String(value) });
      files.set(target, String(value));
    },
  };
  fs.promises = {
    readFile: async (...args) => fs.readFileSync(...args),
    mkdir: async (...args) => fs.mkdirSync(...args),
    writeFile: async (...args) => fs.writeFileSync(...args),
  };
  const github = {
    rest: {
      pulls: {
        async get(parameters) {
          const index = calls.gets.push(parameters) - 1;
          return {
            data: { head: { sha: heads[Math.min(index, heads.length - 1)] }, state: "open" },
          };
        },
      },
      issues: {
        async listComments(parameters) {
          calls.lists.push(parameters);
          return { data: structuredClone(comments) };
        },
        async createComment(parameters) {
          calls.creates.push(parameters);
          const comment = {
            id: 900 + comments.length,
            user: { type: "Bot" },
            body: parameters.body,
          };
          comments.push(comment);
          return { data: comment };
        },
        async updateComment(parameters) {
          calls.updates.push(parameters);
          const comment = comments.find((item) => item.id === parameters.comment_id);
          assert.ok(comment, "Only an existing comment may be updated");
          comment.body = parameters.body;
          return { data: comment };
        },
      },
    },
    async paginate(method, parameters) {
      return (await method(parameters)).data;
    },
  };
  const core = {
    info() {},
    warning() {},
    setFailed: (message) => calls.failures.push(message),
    summary: {
      addRaw(value) {
        summary.push(value);
        return this;
      },
      async write() {},
    },
  };
  const context = {
    repo: { owner: "example", repo: "devfeed.tech" },
    issue: { number: 304 },
    runId: 12345,
    serverUrl: "https://github.com",
    eventName: "pull_request",
    sha,
    payload: {
      repository: { full_name: "example/devfeed.tech" },
      pull_request: {
        number: 304,
        state: "open",
        base: { repo: { full_name: "example/devfeed.tech" } },
        head: {
          sha,
          repo: { full_name: fork ? "fork/devfeed.tech" : "example/devfeed.tech", fork },
        },
      },
    },
  };
  const process = {
    env: { GITHUB_RUN_ATTEMPT: attempt, PROFILE_RESULT: "success", TEST_RESULT: "success" },
  };
  return {
    files,
    calls,
    context,
    process,
    summary,
    async run() {
      await script()(
        github,
        context,
        core,
        (name) => (name.replace(/^node:/, "") === "fs" ? fs : require(name)),
        process,
      );
      return calls.creates.at(-1)?.body ?? calls.updates.at(-1)?.body ?? summary.join("\n");
    },
  };
}

// Expectations below exercise the exact inline script, including its GitHub API writes.
test("the summary separates after-minus-before deltas from positive savings and negative overhead", async () => {
  const fixture = harness();
  const body = await fixture.run();
  assert.ok(body.startsWith(marker));
  assert.match(body, /100[,.]?000/);
  assert.match(body, /300[,.]?000/);
  assert.match(body, /Read result: \*\*IMPROVED\*\*/);
  assert.match(body, /Write result: \*\*UNVERIFIED\*\*/);
  assert.doesNotMatch(body, /\*\*PASS\b/);
  assert.match(body, /\+50\.0%/);
  assert.match(body, /Delta \(after - before\) \| Improvement/);
  assert.match(body, /positive means less time\/work; negative means slower or higher cost/);
  assert.match(body, /writer.phase|paced|pacing/i);
  assert.match(body, /roll.?back|rolled.back|fsync/i);
  assert.match(body, /Report pair median[^\n]+-50\.000 ms \| \+50\.0%/);
  assert.match(body, /Rolled-back synthetic write median[^\n]+\+1\.000 ms[^\n]+-25\.0%/);
  assert.match(body, /WAL snapshot[^\n]+\+0\.488 KiB[^\n]+-50\.0%/);
  assert.match(body, /cannot establish durable write throughput or overall production benefit/);
  assert.match(body, new RegExp(sha));
  assert.ok(
    body.includes(
      "[Workflow, artifacts and full plans](https://github.com/example/devfeed.tech/actions/runs/12345)",
    ),
  );
  assert.equal(fixture.calls.creates.length, 1);
  assert.equal(fixture.calls.updates.length, 0);
  assert.equal(fixture.calls.failures.length, 0);
  assert.ok(fixture.calls.writes.some((entry) => entry.file === "summary/topic-reports.md"));
});

test("faster reads do not claim overall success when committed write costs are absent", async () => {
  const fixture = harness();
  const body = await fixture.run();
  assert.match(body, /Read result: \*\*IMPROVED\*\*/);
  assert.match(body, /Write result: \*\*UNVERIFIED\*\*/);
  assert.match(body, /Committed append, settlement, correction and checkpoint costs are absent/);
  assert.doesNotMatch(body, /\*\*PASS\b|overall benefit verified/i);
});

test("a valid read slowdown is disclosed instead of being called an improvement", async () => {
  const fixture = harness();
  changeArtifacts(fixture, (data) => {
    data.concurrent.facts.median_report_pair_ms = 125;
    data.improvement.report_runtime_lower = false;
  });
  const body = await fixture.run();
  assert.match(body, /Read result: \*\*MIXED\*\*/);
  assert.match(body, /Report pair median[^\n]+\+25\.000 ms[^\n]+-25\.0%/);
  assert.doesNotMatch(body, /\*\*PASS\b|Unavailable/);
  assert.equal(fixture.calls.failures.length, 0);
});

test("zero baselines preserve absolute deltas without inventing a percentage", async () => {
  const fixture = harness();
  changeArtifacts(fixture, (data) => {
    data.concurrent.legacy.ingestion.median_ms = 0;
    data.concurrent.legacy.ingestion.p95_ms = 0;
    data.concurrent.facts.ingestion.p95_ms = 0;
  });
  const body = await fixture.run();
  assert.match(
    body,
    /Rolled-back synthetic write median[^\n]+\+5\.000 ms[^\n]+N\/A \(baseline zero\)/,
  );
  assert.match(body, /Rolled-back synthetic write p95[^\n]+\+0\.000 ms \| 0\.0%/);
  assert.doesNotMatch(body, /NaN|Infinity/);
});

test("committed append overhead is visible even when every read check improves", async () => {
  const fixture = harness();
  changeArtifacts(fixture, (data) => {
    data.durable_writes = committedProfiles();
  });
  const body = await fixture.run();
  assert.match(
    body,
    /Read result: \*\*IMPROVED\*\*\. Write result: \*\*HIGHER LATENCY OBSERVED\*\*/,
  );
  assert.match(body, /Committed topic writes: 5 calls per workflow/);
  assert.match(body, /Committed topic writes: 20 calls per workflow/);
  assert.match(body, /2,400 committed transactions per strategy/);
  assert.match(body, /Append median \(commit included\)[^\n]+\+0\.500 ms[^\n]+-50\.0%/);
  assert.match(body, /Committed writes WAL snapshot[^\n]+\+1\.000 KiB[^\n]+-100\.0%/);
  assert.match(body, /WAL full-page images: 1 → 2/);
  assert.match(body, /not a stable amplification ratio/);
  assert.doesNotMatch(body, /\*\*PASS\b/);
  assert.equal(fixture.calls.failures.length, 0);
});

test("lower sampled committed latency still makes no overall production claim", async () => {
  const fixture = harness();
  changeArtifacts(fixture, (data) => {
    data.durable_writes = committedProfiles({ slower: false });
  });
  const body = await fixture.run();
  assert.match(body, /Write result: \*\*NO HIGHER LATENCY IN THESE SAMPLES\*\*/);
  assert.match(body, /Append median \(commit included\)[^\n]+-0\.500 ms \| \+50\.0%/);
  assert.match(
    body,
    /do not measure full ingestion-pipeline capacity or establish an overall production benefit/,
  );
  assert.doesNotMatch(body, /\*\*PASS\b/);
});

test("committed phase time and thread CPU overhead stay visible alongside rollback diagnostics", async () => {
  const fixture = harness();
  changeArtifacts(fixture, (data) => {
    data.durable_writes = committedProfiles();
  });
  const body = await fixture.run();
  assert.match(
    body,
    /Committed lifecycle phase \| 3\.000 s \| 4\.500 s[^\n]+\+1\.500 s[^\n]+-50\.0%/,
  );
  assert.match(body, /Application thread CPU \| 1\.000 s \| 1\.500 s[^\n]+\+0\.500 s[^\n]+-50\.0%/);
  assert.match(body, /Concurrent report diagnostics use paced writers and roll back/);
  assert.match(body, /Committed lifecycle samples include COMMIT/);
  assert.match(
    body,
    /phase elapsed time and application thread CPU compare fixed logical work, not pipeline capacity/,
  );
  assert.doesNotMatch(body, /every transaction ends with rollback|\*\*PASS\b/);
});

test("one measured fixture cannot verify writes for a second fixture without committed evidence", async () => {
  const fixture = harness();
  const target = [...fixture.files.keys()].find((file) => file.includes("100000"));
  const data = JSON.parse(fixture.files.get(target));
  data.durable_writes = committedProfiles();
  fixture.files.set(target, JSON.stringify(data));
  const body = await fixture.run();
  assert.match(body, /Read result: \*\*IMPROVED\*\*\. Write result: \*\*UNVERIFIED\*\*/);
  assert.match(body, /Committed topic writes: 20 calls per workflow/);
  assert.match(body, /Committed append, settlement, correction and checkpoint costs are absent/);
  assert.equal(fixture.calls.failures.length, 0);
});

for (const [label, change] of [
  [
    "missing long workflow",
    (profiles) => {
      profiles.pop();
    },
  ],
  [
    "unequal source histories",
    (profiles) => {
      profiles[0].source_states_equal = false;
    },
  ],
  [
    "unverified projection",
    (profiles) => {
      profiles[0].projection_verified = false;
    },
  ],
  [
    "rolled-back transactions",
    (profiles) => {
      profiles[0].strategies.facts.committed = false;
    },
  ],
  [
    "missing commits",
    (profiles) => {
      profiles[0].strategies.facts.committed_transactions--;
    },
  ],
  [
    "missing operation",
    (profiles) => {
      delete profiles[0].strategies.facts.operations.append;
    },
  ],
  [
    "non-numeric WAL",
    (profiles) => {
      profiles[0].strategies.facts.wal_bytes = "2048";
    },
  ],
  [
    "nonalternating order",
    (profiles) => {
      profiles[0].paired_order[1] = ["legacy", "facts"];
    },
  ],
]) {
  test(`invalid committed-write evidence rejects ${label}`, async () => {
    const fixture = harness();
    changeArtifacts(fixture, (data) => {
      data.durable_writes = committedProfiles();
      change(data.durable_writes);
    });
    const body = await fixture.run();
    assert.match(body, /Read result: \*\*UNVERIFIED\*\*/);
    assert.match(body, /Invalid committed-write evidence/);
    assert.equal(fixture.calls.failures.length, 1);
  });
}

for (const [name, mutate, message] of [
  ["missing", (fixture, target) => fixture.files.delete(target), /missing|unavailable|absent/i],
  [
    "malformed",
    (fixture, target) => fixture.files.set(target, "{broken"),
    /invalid|malformed|unavailable|parse/i,
  ],
  [
    "incomplete",
    (fixture, target) => {
      const result = JSON.parse(fixture.files.get(target));
      result.complete = false;
      fixture.files.set(target, JSON.stringify(result));
    },
    /incomplete|unavailable|invalid/i,
  ],
]) {
  test(`the summary identifies a ${name} current-attempt artifact`, async () => {
    const fixture = harness();
    const target = [...fixture.files.keys()].find((file) => file.includes("300000"));
    mutate(fixture, target);
    const body = await fixture.run();
    assert.match(body, message);
    assert.match(body, /300[,.]?000/);
    assert.equal(fixture.calls.creates.length, 1);
    assert.equal(fixture.calls.failures.length, 1);
  });
}

for (const [name, mutate] of [
  ["non-native identity", (result) => (result.client_architecture = "x86_64")],
  [
    "PostgreSQL 18 identity",
    (result) => (result.server.version = "PostgreSQL 18.0 on aarch64-unknown-linux-gnu"),
  ],
  [
    "AMD64 server identity",
    (result) => (result.server.version = "PostgreSQL 17.11 on x86_64-pc-linux-gnu"),
  ],
  ["non-numeric metrics", (result) => (result.concurrent.facts.median_report_pair_ms = "50")],
  ["missing parity", (result) => (result.reports[1].exact_output_parity = false)],
  ["missing correction evidence", (result) => delete result.correction_plans.facts.Plan],
  [
    "inconsistent improvement checks",
    (result) => (result.improvement.report_runtime_lower = false),
  ],
]) {
  test(`invalid ${name} cannot produce a passing result`, async () => {
    const fixture = harness();
    const target = [...fixture.files.keys()][0];
    const result = JSON.parse(fixture.files.get(target));
    mutate(result);
    fixture.files.set(target, JSON.stringify(result));
    const body = await fixture.run();
    assert.match(body, /Read result: \*\*UNVERIFIED\*\*/);
    assert.match(body, /Unavailable/);
    assert.doesNotMatch(body, /NaN|Infinity/);
    assert.equal(fixture.calls.failures.length, 1);
  });
}

test("oversized artifacts are rejected before their contents are read", async () => {
  const fixture = harness();
  const target = [...fixture.files.keys()][0];
  fixture.files.set(target, "x".repeat(4 * 1024 * 1024 + 1));
  const body = await fixture.run();
  assert.match(body, /Oversized artifact/);
  assert.ok(!fixture.calls.reads.includes(target));
  assert.equal(fixture.calls.failures.length, 1);
});

for (const articles of attempts) {
  test(`a single flat ${articles} artifact retains its results when the other matrix job fails`, async () => {
    const fixture = harness();
    fixture.files.clear();
    fixture.files.set("report/topic-reports.json", JSON.stringify(artifact(articles)));
    fixture.process.env.PROFILE_RESULT = "failure";
    const body = await fixture.run();
    const section = (count) =>
      body.split(`#### ${count.toLocaleString("en-US")} articles`)[1].split("####")[0];
    assert.match(body, /Read result: \*\*UNVERIFIED\*\*/);
    assert.match(section(articles), /Report pair median/);
    const missing = section(articles === 100000 ? 300000 : 100000);
    assert.match(missing, /Unavailable/);
    assert.doesNotMatch(missing, /Report pair median/);
    assert.equal(fixture.calls.creates.length, 1);
    assert.equal(fixture.calls.failures.length, 1);
  });
}

test("an artifact from a previous attempt cannot replace a missing current result", async () => {
  const fixture = harness({ attempt: "3" });
  const target = [...fixture.files.keys()].find((file) => file.includes("300000"));
  fixture.files.delete(target);
  fixture.files.set(target.replace("-3/", "-2/"), JSON.stringify(artifact(300000)));
  const body = await fixture.run();
  assert.match(body, /missing|unavailable|absent/i);
  assert.ok(fixture.calls.reads.every((file) => !file.includes("-arm64-2/")));
});

test("a failed profile or test job is reported as failed even when artifacts are readable", async () => {
  for (const variable of ["PROFILE_RESULT", "TEST_RESULT"]) {
    const fixture = harness();
    fixture.process.env[variable] = "failure";
    const body = await fixture.run();
    assert.match(body, /fail/i);
    assert.equal(fixture.calls.creates.length, 1);
  }
});

test("an obsolete head is never published before listing comments", async () => {
  const fixture = harness({ heads: ["newer-head"] });
  await fixture.run();
  assert.equal(fixture.calls.lists.length, 0);
  assert.equal(fixture.calls.creates.length, 0);
  assert.equal(fixture.calls.updates.length, 0);
});

test("an obsolete head is never published when it changes while comments are listed", async () => {
  const fixture = harness({ heads: [sha, "newer-head"] });
  await fixture.run();
  assert.equal(fixture.calls.lists.length, 1);
  assert.equal(fixture.calls.creates.length, 0);
  assert.equal(fixture.calls.updates.length, 0);
});

test("fork pull requests receive a summary without attempted comment writes", async () => {
  const fixture = harness({ fork: true });
  await fixture.run();
  assert.ok(fixture.calls.writes.some((entry) => entry.file === "summary/topic-reports.md"));
  assert.ok(fixture.summary.length > 0);
  assert.equal(fixture.calls.creates.length, 0);
  assert.equal(fixture.calls.updates.length, 0);
});

test("the existing bot marker is updated while human comments remain untouched", async () => {
  const fixture = harness({
    comments: [
      { id: 7, user: { type: "User" }, body: `${marker}\nA human note` },
      { id: 8, user: { type: "Bot" }, body: "Unrelated bot comment" },
      { id: 9, user: { type: "Bot" }, body: `${marker}\nPrior result` },
    ],
  });
  await fixture.run();
  assert.equal(fixture.calls.creates.length, 0);
  assert.equal(fixture.calls.updates.length, 1);
  assert.equal(fixture.calls.updates[0].comment_id, 9);
});

test("rerunning the same result updates one marker comment instead of creating a duplicate", async () => {
  const fixture = harness();
  await fixture.run();
  await fixture.run();
  assert.equal(fixture.calls.creates.length, 1);
  assert.equal(fixture.calls.updates.length, 1);
  assert.equal(fixture.calls.updates[0].comment_id, 900);
});
