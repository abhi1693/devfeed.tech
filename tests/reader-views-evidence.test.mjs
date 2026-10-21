import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  readerViewsReportDirectory,
  runReaderViewCheck,
  savedReaderViewScenarios,
  writeReaderViewReport,
} from "../scripts/testing/reader-views-evidence.mjs";

const repository = fileURLToPath(new URL("../", import.meta.url));
const runtimes = ["web", "chrome", "edge"];
const expectedScenarios = [
  "account-lifecycle",
  "save-recovery",
  "accessibility-guest",
  "accessibility-member",
  "column-budgets",
];
const revision = () =>
  execFileSync("git", ["rev-parse", "HEAD"], { cwd: repository, encoding: "utf8" }).trim();
const summary = async (directory) =>
  JSON.parse(await readFile(path.join(directory, "summary.json"), "utf8"));

async function isolated(t) {
  const directory = await mkdtemp(path.join(tmpdir(), "devfeed-reader-views-evidence-"));
  const keys = [
    "DEVFEED_READER_VIEWS_REPORT_DIR",
    "GITHUB_SHA",
    "GITHUB_RUN_ATTEMPT",
    "DEVFEED_READER_VIEWS_TEST_SECRET",
  ];
  const previous = new Map(keys.map((key) => [key, process.env[key]]));
  t.after(async () => {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(directory, { recursive: true, force: true });
  });
  process.env.DEVFEED_READER_VIEWS_REPORT_DIR = directory;
  delete process.env.GITHUB_SHA;
  delete process.env.GITHUB_RUN_ATTEMPT;
  return directory;
}

test("uses the ignored checkout report root by default and a distinct directory for every runtime and scenario", async (t) => {
  const temporary = await isolated(t);
  assert.deepEqual(savedReaderViewScenarios, expectedScenarios);
  const destinations = [];
  for (const runtime of runtimes) {
    for (const scenario of expectedScenarios) {
      const directory = readerViewsReportDirectory(runtime, scenario);
      assert.equal(directory, path.join(temporary, runtime, scenario));
      destinations.push(directory);
    }
  }
  assert.equal(new Set(destinations).size, 15);
  delete process.env.DEVFEED_READER_VIEWS_REPORT_DIR;
  assert.equal(
    readerViewsReportDirectory("web", "account-lifecycle"),
    path.join(repository, "reports", "reader-views", "web", "account-lifecycle"),
  );
});

test("persists complete observed proof and trusted source identity without unrelated environment values", async (t) => {
  await isolated(t);
  const head = revision();
  const sentinel = "synthetic-secret-that-must-not-be-copied-to-artifacts";
  process.env.DEVFEED_READER_VIEWS_TEST_SECRET = sentinel;
  process.env.GITHUB_SHA = head;
  process.env.GITHUB_RUN_ATTEMPT = "7";
  const proof = {
    status: "passed",
    checks: {
      account: { layout: "compact", retained_article_ids: ["article-a", "article-b"] },
      guest_storage_unchanged: true,
    },
    observations: ["Unicode 日本語", { keyboard: ["Tab", "Enter"] }],
    schema_version: 99,
    runtime: "forged",
    scenario: "forged",
    source_revision: "forged",
    source_dirty: "forged",
    run_attempt: "forged",
    recorded_at: "forged",
  };
  const directory = readerViewsReportDirectory("edge", "account-lifecycle");
  const started = Date.now();
  const returned = await writeReaderViewReport(directory, proof);
  const report = await summary(directory);
  assert.deepEqual(returned, report);
  assert.equal(report.status, "passed");
  assert.deepEqual(report.checks, proof.checks);
  assert.deepEqual(report.observations, proof.observations);
  assert.equal(report.schema_version, 1);
  assert.equal(report.runtime, "edge");
  assert.equal(report.scenario, "account-lifecycle");
  assert.equal(report.source_revision, head);
  assert.equal(typeof report.source_dirty, "boolean");
  assert.equal(
    report.source_dirty,
    execFileSync("git", ["status", "--porcelain=v1", "--untracked-files=normal"], {
      cwd: repository,
      encoding: "utf8",
    }).trim() !== "",
  );
  assert.equal(report.run_attempt, "7");
  assert.ok(
    Date.parse(report.recorded_at) >= started && Date.parse(report.recorded_at) <= Date.now(),
  );
  assert.equal(JSON.stringify(report).includes(sentinel), false);
  assert.equal(Object.hasOwn(report, "env"), false);
  assert.equal(Object.hasOwn(report, "DEVFEED_READER_VIEWS_TEST_SECRET"), false);
  assert.deepEqual(await readdir(directory), ["summary.json"]);
});

test("durably replaces a running summary with a failed observation without altering other runtime or scenario reports", async (t) => {
  await isolated(t);
  const selected = readerViewsReportDirectory("web", "save-recovery");
  const others = [
    readerViewsReportDirectory("chrome", "save-recovery"),
    readerViewsReportDirectory("web", "accessibility-member"),
  ];
  await Promise.all(
    others.map((directory) =>
      writeReaderViewReport(directory, { status: "passed", evidence: directory }),
    ),
  );
  const preserved = await Promise.all(
    others.map((directory) => readFile(path.join(directory, "summary.json"), "utf8")),
  );
  await writeReaderViewReport(selected, { status: "running", checks: [] });
  assert.equal((await summary(selected)).status, "running");
  await writeReaderViewReport(selected, {
    status: "failed",
    failure: { check: "rejected-save-rollback", message: "The prior layout was not restored." },
    checks: ["initial-layout"],
  });
  const failed = await summary(selected);
  assert.equal(failed.status, "failed");
  assert.deepEqual(failed.failure, {
    check: "rejected-save-rollback",
    message: "The prior layout was not restored.",
  });
  assert.deepEqual(failed.checks, ["initial-layout"]);
  assert.equal(failed.run_attempt, "local");
  assert.deepEqual(
    await Promise.all(
      others.map((directory) => readFile(path.join(directory, "summary.json"), "utf8")),
    ),
    preserved,
  );
  assert.deepEqual(await readdir(selected), ["summary.json"]);
});

test("rejects unsupported runtime, scenario, and output paths before creating evidence", async (t) => {
  const temporary = await isolated(t);
  for (const runtime of [undefined, "firefox", "../web", "WEB"])
    assert.throws(() => readerViewsReportDirectory(runtime, "save-recovery"), /Unsupported/);
  for (const scenario of [undefined, "anything", "../account-lifecycle", "account-lifecycle/extra"])
    assert.throws(() => readerViewsReportDirectory("web", scenario), /Unsupported/);
  const outside = path.join(temporary, "other-root", "web", "save-recovery");
  await assert.rejects(
    writeReaderViewReport(outside, { status: "passed" }),
    /configured report directory/,
  );
  await assert.rejects(
    writeReaderViewReport(path.join(temporary, "firefox", "save-recovery"), { status: "passed" }),
    /Unsupported/,
  );
  assert.deepEqual(await readdir(temporary), []);
});

test("rejects missing and unobserved statuses without replacing an existing summary", async (t) => {
  await isolated(t);
  const directory = readerViewsReportDirectory("chrome", "accessibility-guest");
  await writeReaderViewReport(directory, { status: "running" });
  const previous = await readFile(path.join(directory, "summary.json"), "utf8");
  for (const proof of [null, {}, { status: "complete" }, { status: "PASSED" }, { status: false }]) {
    await assert.rejects(writeReaderViewReport(directory, proof), /observed status/);
    assert.equal(await readFile(path.join(directory, "summary.json"), "utf8"), previous);
  }
});

test("rejects mismatched CI source identity and invalid run attempts without replacing evidence", async (t) => {
  await isolated(t);
  const directory = readerViewsReportDirectory("edge", "save-recovery");
  await writeReaderViewReport(directory, { status: "running" });
  const previous = await readFile(path.join(directory, "summary.json"), "utf8");
  process.env.GITHUB_SHA = revision() === "0".repeat(40) ? "1".repeat(40) : "0".repeat(40);
  await assert.rejects(
    writeReaderViewReport(directory, { status: "passed" }),
    /checked-out CI revision/,
  );
  delete process.env.GITHUB_SHA;
  for (const attempt of ["0", "-1", "1.5", "unknown", "", " 2", "02"]) {
    process.env.GITHUB_RUN_ATTEMPT = attempt;
    await assert.rejects(
      writeReaderViewReport(directory, { status: "passed" }),
      /Unsupported CI run attempt/,
    );
  }
  assert.equal(await readFile(path.join(directory, "summary.json"), "utf8"), previous);
});

test("preserves the previous summary and cleans temporary files if proof serialization fails", async (t) => {
  await isolated(t);
  const directory = readerViewsReportDirectory("web", "account-lifecycle");
  await writeReaderViewReport(directory, { status: "running" });
  const previous = await readFile(path.join(directory, "summary.json"), "utf8");
  const cyclic = { status: "passed" };
  cyclic.details = cyclic;
  await assert.rejects(writeReaderViewReport(directory, cyclic), /circular/i);
  assert.equal(await readFile(path.join(directory, "summary.json"), "utf8"), previous);
  assert.deepEqual(await readdir(directory), ["summary.json"]);
});

test("surfaces filesystem write failure without leaving a temporary success report", async (t) => {
  await isolated(t);
  const directory = readerViewsReportDirectory("chrome", "account-lifecycle");
  await mkdir(directory, { recursive: true });
  const blocked = path.join(directory, "summary.json");
  await mkdir(blocked);
  await writeFile(path.join(blocked, "previous-proof"), "preserved");
  await assert.rejects(writeReaderViewReport(directory, { status: "passed" }), { code: "EISDIR" });
  assert.equal(await readFile(path.join(blocked, "previous-proof"), "utf8"), "preserved");
  assert.deepEqual(await readdir(directory), ["summary.json"]);
});

test("publishes running evidence before the browser check and then persists its complete observations", async (t) => {
  await isolated(t);
  const directory = readerViewsReportDirectory("chrome", "column-budgets");
  const observations = {
    limits: { active_requests: 3, articles_per_column: 72, total_articles: 864 },
    checks: ["independent-pagination", "bounded-retention"],
  };
  let observedRunning = false;
  const returned = await runReaderViewCheck("chrome", "column-budgets", async (destination) => {
    assert.equal(destination, directory);
    const running = await summary(destination);
    assert.equal(running.status, "running");
    assert.equal(running.runtime, "chrome");
    assert.equal(running.scenario, "column-budgets");
    observedRunning = true;
    return observations;
  });
  assert.equal(observedRunning, true);
  const report = await summary(directory);
  assert.deepEqual(returned, report);
  assert.equal(report.status, "passed");
  assert.deepEqual(report.limits, observations.limits);
  assert.deepEqual(report.checks, observations.checks);
  assert.deepEqual(await readdir(directory), ["summary.json"]);
});

test("rethrows the original browser error after durably replacing an earlier pass with failed evidence", async (t) => {
  await isolated(t);
  const directory = readerViewsReportDirectory("edge", "account-lifecycle");
  await writeReaderViewReport(directory, {
    status: "passed",
    previous_run_only: "This must not survive a failing rerun.",
    checks: ["old-observation"],
  });
  const original = new Error("The signed-out account retained private articles.");
  await assert.rejects(
    runReaderViewCheck("edge", "account-lifecycle", async (destination) => {
      const running = await summary(destination);
      assert.equal(running.status, "running");
      assert.equal(Object.hasOwn(running, "previous_run_only"), false);
      throw original;
    }),
    (error) => error === original,
  );
  const failed = await summary(directory);
  assert.equal(failed.status, "failed");
  assert.equal(failed.error, original.message);
  assert.equal(Object.hasOwn(failed, "previous_run_only"), false);
  assert.equal(Object.hasOwn(failed, "checks"), false);
  assert.equal(failed.runtime, "edge");
  assert.equal(failed.scenario, "account-lifecycle");
  assert.equal(failed.source_revision, revision());
  assert.deepEqual(await readdir(directory), ["summary.json"]);
});
