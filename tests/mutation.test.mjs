import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync } from "node:fs";
import {
  checkReport,
  comparison,
  policy,
  selectGroups,
  suiteCoverage,
  runCommand,
} from "../scripts/ci/mutation.mjs";

const all = Object.keys(policy);

test("every configured file has a nonzero gate and an existing focused suite", () => {
  for (const group of Object.values(policy)) {
    assert.ok(group.tests.length > 0);
    for (const name of group.tests)
      assert.ok(
        ["ts", "tsx"].some((extension) =>
          existsSync(new URL(`../apps/web/tests/${name}.test.${extension}`, import.meta.url)),
        ),
      );
    for (const [file, minimum] of Object.entries(group.files)) {
      assert.ok(existsSync(new URL(`../${file}`, import.meta.url)), file);
      assert.ok(minimum > 0 && minimum <= 100, file);
    }
  }
});

test("bounds runner time and distinguishes failures from successful completion", async () => {
  const success = await runCommand(process.execPath, ["-e", "process.exit(0)"], 3000);
  assert.equal(success.status, 0);
  assert.equal(success.expired, false);
  const expired = await runCommand(process.execPath, ["-e", "setInterval(() => {}, 1000)"], 100);
  assert.equal(expired.expired, true);
  assert.equal(expired.signal, "SIGKILL");
});

test("suite coverage preserves every covered mutant without filtering nested test names", () => {
  const input = {
    tests: [{ id: "suite nested test" }],
    mutantCoverage: { static: { 1: 2 }, perTest: { first: { 1: 3, 2: 1 }, second: { 2: 2 } } },
  };
  const result = suiteCoverage(input);
  assert.deepEqual(result.tests, input.tests);
  assert.deepEqual(result.mutantCoverage, { static: { 1: 5, 2: 3 }, perTest: {} });
  assert.deepEqual(input.mutantCoverage.static, { 1: 2 });
  assert.deepEqual(suiteCoverage({ status: "Error" }), { status: "Error" });
});
function report(group, statuses = ["Killed"]) {
  return {
    files: Object.fromEntries(
      Object.keys(policy[group].files).map((file) => [
        file,
        { mutants: statuses.map((status, id) => ({ id: String(id), status, testsCompleted: 1 })) },
      ]),
    ),
  };
}

test("selects only the owning groups for target changes", () => {
  assert.deepEqual(selectGroups(["apps/web/src/lib/feed-query.ts"]), ["feed"]);
  assert.deepEqual(
    selectGroups(["packages/telemetry/src/privacy.ts", "apps/web/src/lib/server/config.ts"]),
    ["auth", "telemetry"],
  );
});

test("checks shared dependencies, tests and configuration conservatively", () => {
  for (const file of [
    "package-lock.json",
    "apps/web/tests/new-security.test.ts",
    "apps/web/src/lib/server/user.ts",
    "packages/telemetry/src/propagation.ts",
    "scripts/ci/stryker.config.mjs",
    "scripts/ci/mutation-policy.json",
    ".github/workflows/mutation.yml",
    ".github/workflows/ci.yml",
  ])
    assert.deepEqual(selectGroups([file]), all, file);
  assert.deepEqual(selectGroups(["README.md", "apps/api/src/devfeed_api/main.py"]), []);
});

test("compares PR and merge queue bases and runs all modules nightly", () => {
  const base = "a".repeat(40);
  const head = "b".repeat(40);
  assert.equal(
    comparison("pull_request", { pull_request: { base: { sha: base } } }, head),
    `${base}...${head}`,
  );
  assert.equal(
    comparison("merge_group", { merge_group: { base_sha: base } }, head),
    `${base}...${head}`,
  );
  assert.equal(comparison("schedule", {}, head), null);
  assert.equal(comparison("workflow_dispatch", {}, head), null);
  assert.throws(() => comparison("pull_request", {}, head), /valid base/);
  assert.throws(() => comparison("push", {}, head), /valid base/);
  assert.throws(
    () => comparison("pull_request", { pull_request: { base: { sha: "--help" } } }, head),
    /valid base/,
  );
});

test("scores killed and timeout mutants and exposes survivors and uncovered code", () => {
  const [file] = checkReport(
    report("feed", ["Killed", "Timeout", "Survived", "NoCoverage", "CompileError"]),
    "feed",
  );
  assert.equal(file.score, 50);
  assert.equal(file.total, 4);
  assert.equal(file.Survived, 1);
  assert.equal(file.NoCoverage, 1);
  assert.equal(file.CompileError, 1);
  assert.equal(checkReport(report("feed"), "feed")[0].passed, true);
});

test("enforces each file minimum so a strong neighbor cannot mask a regression", () => {
  const result = report("auth");
  result.files[Object.keys(policy.auth.files)[1]].mutants = [
    { id: "0", status: "Survived", testsCompleted: 1 },
  ];
  const files = checkReport(result, "auth");
  assert.equal(files[0].passed, true);
  assert.equal(files[1].passed, false);
});

test("rejects missing, empty, duplicate and incomplete reports", () => {
  for (const input of [null, {}, { files: {} }]) assert.throws(() => checkReport(input, "feed"));
  for (const statuses of [[], ["CompileError"], ["Pending"], ["RuntimeError"], ["Ignored"]])
    assert.throws(() => checkReport(report("feed", statuses), "feed"));
  const duplicate = report("feed", ["Killed", "Killed"]);
  Object.values(duplicate.files)[0].mutants[1].id = "0";
  assert.throws(() => checkReport(duplicate, "feed"), /invalid mutation/);
  const extra = report("feed");
  extra.files["unexpected.ts"] = { mutants: [{ id: "0", status: "Killed" }] };
  assert.throws(() => checkReport(extra, "feed"), /configured files/);
  const untested = report("feed", ["Survived"]);
  Object.values(untested.files)[0].mutants[0].testsCompleted = 0;
  assert.throws(() => checkReport(untested, "feed"), /ran no tests/);
});
