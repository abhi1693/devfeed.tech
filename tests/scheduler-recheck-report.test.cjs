const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { renderReport } = require("../scripts/render_scheduler_recheck_report.cjs");

const context = {
  runUrl: "https://github.com/example/devfeed/actions/runs/123",
  headSha: "a".repeat(40),
  baseSha: "b".repeat(40),
  conclusion: "success",
};

function report(articles = 100000) {
  return {
    complete: true,
    checkpoint_before_workload: true,
    hardware: { architecture: "aarch64" },
    resources: { postgres: { version: "PostgreSQL 17.11 on aarch64-unknown-linux-gnu" } },
    dataset: { articles, article_analysis_jobs: 200000 },
    production_2026_10_24_0928_utc: { articles: 92581, article_analysis_jobs: 156004 },
    below_production_population: {},
    workload: {
      scale: articles / 150000,
      ticks: 1,
      batch: 50,
      ingest_per_tick: 10,
      history_bytes: 8192,
    },
    ticks: [
      {
        outcome: { articles_checked: 50, articles_published: 0, articles_rejected: 0 },
        ingested: 10,
        ingested_projected: 10,
        ingestion_overlapped_scheduler: true,
        active_analysis_unchanged: true,
        cohort_clock_visible: true,
        cohort_retry_transition_correct: true,
        cohort_events: 0,
        hydration: {},
        publisher_deleted_rows: {},
      },
    ],
    plans: { scheduler_due_candidates: { plan: [{ Plan: { "Actual Rows": 50 } }] } },
    bounded_failures: [],
    workload_seconds: 10,
    application_cpu_seconds: 20,
    postgres_workload_delta: {
      calls: 1000,
      shared_blks_hit: 2000,
      shared_blks_read: 0,
      temp_blks_written: 0,
      wal_records: 100,
      wal_fpi: 2,
      wal_bytes: 4096,
    },
  };
}

function pair(articles) {
  return { baseline: report(articles), candidate: report(articles) };
}

function row(markdown, metric) {
  return markdown.split("\n").find((line) => line.startsWith(`| ${metric} |`));
}

function runCLI(directory, arguments_ = ["--articles", "100000", "--directory", "."]) {
  return spawnSync(
    process.execPath,
    [path.resolve(__dirname, "../scripts/render_scheduler_recheck_report.cjs"), ...arguments_],
    { cwd: directory, encoding: "utf8" },
  );
}

function temporaryDirectory(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devfeed-recheck-report-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("reports signed reductions and regressions with immutable source identities", () => {
  const data = pair();
  data.baseline.ticks[0].cohort_events = 50;
  data.baseline.ticks[0].hydration.cohort_ids = 50;
  data.baseline.bounded_failures = [
    { tick: 1, reasons: ["clock-only rechecks generated card events or hydration"] },
  ];
  data.candidate.workload_seconds = 5;
  data.candidate.application_cpu_seconds = 25;
  const markdown = renderReport({ ...context, reports: { 100000: data } });
  assert.match(markdown, /Valid controlled comparison/);
  assert.match(row(markdown, "Workload runtime (s)"), /10\.000 \| 5\.000 \| -5\.000 \| \+50\.0%/);
  assert.match(row(markdown, "Application CPU (s)"), /20\.000 \| 25\.000 \| \+5\.000 \| -25\.0%/);
  assert.match(row(markdown, "Clock-only events"), /50 \| 0 \| -50 \| \+100\.0%/);
  assert.match(markdown, /expected clock-only event\/hydration amplification/);
  assert.match(markdown, new RegExp(context.baseSha));
  assert.match(markdown, new RegExp(context.headSha));
  assert.match(
    markdown,
    /\[Workflow run\]\(https:\/\/github.com\/example\/devfeed\/actions\/runs\/123\)/,
  );
  assert.doesNotMatch(markdown, /150,000 articles/);
});

test("zero baselines show unchanged zero or a negative new cost without division artifacts", () => {
  const data = pair();
  data.candidate.postgres_workload_delta.temp_blks_written = 3;
  const markdown = renderReport({ ...context, reports: { 100000: data } });
  assert.match(row(markdown, "Shared blocks read"), /0 \| 0 \| 0 \| 0\.0%/);
  assert.match(row(markdown, "Temporary blocks written"), /0 \| 3 \| \+3 \| - \(new cost\)/);
  assert.doesNotMatch(markdown, /NaN|Infinity|undefined/);
});

test("missing candidate data shows partial measurements without a speedup", () => {
  const markdown = renderReport({ ...context, reports: { 100000: { baseline: report() } } });
  assert.match(markdown, /After report missing/);
  assert.match(
    row(markdown, "Workload runtime (s)"),
    /10\.000 \| unavailable \| unavailable \| unavailable/,
  );
  assert.doesNotMatch(markdown, /Valid controlled comparison|\+\d.*%/);
});

for (const [name, mutate, reason] of [
  [
    "incomplete run",
    (data) => {
      data.candidate.complete = false;
    },
    /run incomplete/,
  ],
  [
    "changed population",
    (data) => {
      data.candidate.dataset.article_analysis_jobs++;
    },
    /dataset differ/,
  ],
  [
    "changed workload",
    (data) => {
      data.candidate.workload.history_bytes++;
    },
    /workload differ/,
  ],
  [
    "missing workload",
    (data) => {
      delete data.candidate.workload;
    },
    /workload missing or invalid/,
  ],
  [
    "missing checkpoint",
    (data) => {
      delete data.candidate.checkpoint_before_workload;
    },
    /checkpoint unverified/,
  ],
  [
    "other architecture",
    (data) => {
      data.candidate.hardware.architecture = "x86_64";
    },
    /ARM64 unverified/,
  ],
  [
    "smaller than production",
    (data) => {
      data.candidate.dataset.article_analysis_jobs = 100;
    },
    /population floor/,
  ],
  [
    "bounded-work failure",
    (data) => {
      data.candidate.bounded_failures = [{ reasons: ["provider job changed"] }];
    },
    /bounded-work assertions failed/,
  ],
  [
    "retention writes",
    (data) => {
      data.candidate.ticks[0].publisher_deleted_rows.publisher = 100;
    },
    /retention cleanup/,
  ],
  [
    "empty due plan",
    (data) => {
      data.candidate.plans.scheduler_due_candidates.plan[0].Plan["Actual Rows"] = 0;
    },
    /due-candidate plan/,
  ],
  [
    "missing WAL evidence",
    (data) => {
      delete data.candidate.postgres_workload_delta.wal_bytes;
    },
    /required measurements missing/,
  ],
]) {
  test(`${name} invalidates every improvement cell`, () => {
    const data = pair();
    data.candidate.workload_seconds = 1;
    mutate(data);
    const markdown = renderReport({ ...context, reports: { 100000: data } });
    assert.match(markdown, reason);
    assert.match(markdown, /Unavailable comparison/);
    assert.match(row(markdown, "Workload runtime (s)"), /unavailable \| unavailable \|$/);
    assert.doesNotMatch(markdown, /\+90\.0%|Valid controlled comparison/);
  });
}

test("failed workflows and missing dataset collections never claim a valid comparison", () => {
  const failed = renderReport({ ...context, conclusion: "failure", reports: { 100000: pair() } });
  assert.match(failed, /Workflow conclusion: failure/);
  assert.doesNotMatch(failed, /Valid controlled comparison/);
  assert.match(renderReport({ ...context, reports: {} }), /no dataset reports supplied/);
});

test("supplied scales render independently in numeric order", () => {
  const markdown = renderReport({ ...context, reports: { 150000: pair(150000), 100000: pair() } });
  assert.equal((markdown.match(/Valid controlled comparison/g) ?? []).length, 2);
  assert.ok(markdown.indexOf("100,000 articles") < markdown.indexOf("150,000 articles"));
});

test("matching dataset and workload keys compare by key regardless of JSON insertion order", () => {
  const data = pair();
  data.candidate.dataset = Object.fromEntries(Object.entries(data.candidate.dataset).reverse());
  data.candidate.workload = Object.fromEntries(Object.entries(data.candidate.workload).reverse());
  assert.match(
    renderReport({ ...context, reports: { 100000: data } }),
    /Valid controlled comparison/,
  );
});

test("partially written tick and assertion records render as unavailable instead of throwing", () => {
  const data = pair();
  data.candidate.ticks = [null];
  data.baseline.bounded_failures = [null];
  const markdown = renderReport({ ...context, reports: { 100000: data } });
  assert.match(markdown, /measured tick evidence missing/);
  assert.match(markdown, /bounded-work assertions failed/);
  assert.match(
    row(markdown, "Clock-only events"),
    /0 \| unavailable \| unavailable \| unavailable/,
  );
  assert.doesNotMatch(markdown, /Valid controlled comparison|NaN|undefined/);
});

test("malformed bounded-failure records remain visible without crashing the report", () => {
  for (const bounded of [
    null,
    {},
    [null],
    [{ reasons: {} }],
    [{ reasons: "unexpected failure" }],
  ]) {
    const data = pair();
    data.baseline.bounded_failures = bounded;
    const markdown = renderReport({ ...context, reports: { 100000: data } });
    assert.match(markdown, /Before: bounded-work assertions failed or are missing/);
    assert.match(markdown, /Unavailable comparison/);
    assert.doesNotMatch(markdown, /Valid controlled comparison/);
  }
});

test("CLI writes an unavailable report and exits unsuccessfully when JSON artifacts are missing", (t) => {
  const directory = temporaryDirectory(t);
  fs.writeFileSync(path.join(directory, "baseline.json"), JSON.stringify(report()));
  const output = path.join(directory, "comment.md");
  const result = spawnSync(
    process.execPath,
    [
      path.resolve(__dirname, "../scripts/render_scheduler_recheck_report.cjs"),
      "--articles",
      "100000",
      "--directory",
      directory,
      "--output",
      output,
      "--run-url",
      context.runUrl,
      "--head-sha",
      context.headSha,
      "--base-sha",
      context.baseSha,
    ],
    { cwd: directory, encoding: "utf8" },
  );
  assert.equal(result.status, 1, result.stderr);
  assert.match(fs.readFileSync(output, "utf8"), /candidate report unavailable \(ENOENT\)/);
});

test("CLI renders complete artifacts from the workspace and handles invalid JSON without losing output", (t) => {
  const directory = temporaryDirectory(t);
  const reportDirectory = path.join(directory, "reports/scheduler-rechecks");
  fs.mkdirSync(reportDirectory, { recursive: true });
  for (const label of ["baseline", "candidate"])
    fs.writeFileSync(path.join(reportDirectory, `${label}.json`), JSON.stringify(report()));
  const arguments_ = ["--articles", "100000"];
  const success = runCLI(directory, arguments_);
  assert.equal(success.status, 0, success.stderr);
  assert.match(
    fs.readFileSync(path.join(reportDirectory, "comment.md"), "utf8"),
    /Valid controlled comparison/,
  );
  fs.writeFileSync(path.join(reportDirectory, "candidate.json"), "{incomplete");
  const failed = runCLI(directory, arguments_);
  assert.equal(failed.status, 1, failed.stderr);
  assert.match(fs.readFileSync(path.join(reportDirectory, "comment.md"), "utf8"), /invalid JSON/);
});

test("CLI rejects traversal, sibling-prefix escapes and symlinks without touching their targets", (t) => {
  const directory = temporaryDirectory(t);
  const workspace = path.join(directory, "work");
  const outside = path.join(directory, "work-elsewhere");
  fs.mkdirSync(workspace);
  fs.mkdirSync(outside);
  const target = path.join(outside, "protected.md");
  fs.writeFileSync(target, "Keep this content");
  fs.symlinkSync(outside, path.join(workspace, "linked-directory"));
  fs.symlinkSync(target, path.join(workspace, "linked-file.md"));
  for (const output of [
    "../work-elsewhere/protected.md",
    target,
    "linked-directory/protected.md",
    "linked-file.md",
  ]) {
    const result = runCLI(workspace, [
      "--articles",
      "100000",
      "--directory",
      ".",
      "--output",
      output,
    ]);
    assert.notEqual(result.status, 0, `Accepted unsafe output ${output}`);
    assert.match(result.stderr, /working directory|symbolic links/);
    assert.equal(fs.readFileSync(target, "utf8"), "Keep this content");
  }
  assert.notEqual(runCLI(workspace, ["--articles", "100000", "--directory", outside]).status, 0);
  assert.match(runCLI(workspace, ["--articles", "1"]).stderr, /articles must be 100000 or 150000/);
});
