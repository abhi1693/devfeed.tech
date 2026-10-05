import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { withSuiteCoverage } from "../scripts/ci/mutation-runner.mjs";
import {
  checkReport,
  comparison,
  policy,
  selectGroups,
  suiteCoverage,
  runCommand,
  run,
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
  assert.equal(comparison("push", { before: base }, head), `${base}..${head}`);
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

test("runner adapter preserves binding, options, failures and suite coverage", async () => {
  const options = { timeout: 1000 };
  const coverage = {
    tests: [{ id: "parent > child" }],
    mutantCoverage: { static: {}, perTest: { child: { 1: 2 } } },
  };
  const runner = {
    context: "runner",
    async dryRun(received) {
      assert.equal(this.context, "runner");
      assert.equal(received, options);
      return coverage;
    },
  };
  assert.equal(withSuiteCoverage(runner), runner);
  assert.deepEqual(await runner.dryRun(options), {
    tests: coverage.tests,
    mutantCoverage: { static: { 1: 2 }, perTest: {} },
  });
  const failure = new Error("dry run failed");
  await assert.rejects(
    withSuiteCoverage({
      dryRun: async () => {
        throw failure;
      },
    }).dryRun(options),
    failure,
  );
  const result = { status: "Error" };
  assert.equal(await withSuiteCoverage({ dryRun: async () => result }).dryRun(options), result);
});

test("Stryker configuration uses the actual group and rejects unknown groups", async () => {
  const previous = process.env.MUTATION_GROUP;
  try {
    for (const group of all) {
      process.env.MUTATION_GROUP = group;
      const { default: config } = await import(`../scripts/ci/stryker.config.mjs?group=${group}`);
      assert.deepEqual(config.mutate, Object.keys(policy[group].files));
      assert.equal(config.thresholds.break, Math.min(...Object.values(policy[group].files)));
      assert.equal(config.testRunner, "vitest-suite");
      assert.equal(config.coverageAnalysis, "all");
      assert.equal(config.concurrency, 2);
    }
    process.env.MUTATION_GROUP = "unknown";
    await assert.rejects(import("../scripts/ci/stryker.config.mjs?group=unknown"), /configured/);
  } finally {
    if (previous === undefined) delete process.env.MUTATION_GROUP;
    else process.env.MUTATION_GROUP = previous;
  }
});

test("rejects empty and unknown selections before launching workers", async () => {
  await assert.rejects(run([]), /configured mutation groups/);
  await assert.rejects(run(["feed", "unknown"]), /configured mutation groups/);
});

const root = fileURLToPath(new URL("../", import.meta.url));
function controller(args, env = {}) {
  return spawnSync(process.execPath, ["scripts/ci/mutation.mjs", ...args], {
    cwd: root,
    env: { ...process.env, ...env },
    encoding: "utf8",
    timeout: 10_000,
  });
}

test("selector compares actual commits without PATH dependencies or modifying repository refs", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "devfeed-mutation-"));
  try {
    const objects = path.join(directory, "objects");
    const gitDirectory = spawnSync("/usr/bin/git", ["rev-parse", "--git-common-dir"], {
      cwd: root,
      encoding: "utf8",
    });
    assert.equal(gitDirectory.status, 0);
    // Only object storage is isolated. The actual checkout, index and refs are untouched.
    const env = {
      GIT_OBJECT_DIRECTORY: objects,
      GIT_ALTERNATE_OBJECT_DIRECTORIES: path.resolve(root, gitDirectory.stdout.trim(), "objects"),
      GIT_AUTHOR_NAME: "CI fixture",
      GIT_AUTHOR_EMAIL: "ci@example.invalid",
      GIT_COMMITTER_NAME: "CI fixture",
      GIT_COMMITTER_EMAIL: "ci@example.invalid",
    };
    mkdirSync(objects);
    function git(args, input) {
      const result = spawnSync("/usr/bin/git", args, {
        cwd: root,
        env: { ...process.env, ...env },
        input,
        encoding: "utf8",
      });
      assert.equal(result.status, 0, result.stderr);
      return result.stdout.trim();
    }
    const base = git(["commit-tree", git(["mktree"], ""), "-m", "Empty fixture"]);
    const head = git([
      "commit-tree",
      git(["rev-parse", "HEAD^{tree}"]),
      "-p",
      base,
      "-m",
      "Runtime fixture",
    ]);
    const event = path.join(directory, "event.json");
    const output = path.join(directory, "output");
    const summary = path.join(directory, "summary");
    for (const [before, expected] of [
      [base, all],
      [head, []],
    ]) {
      writeFileSync(event, JSON.stringify({ before }));
      writeFileSync(output, "");
      const result = controller(["select"], {
        ...env,
        PATH: "",
        GITHUB_EVENT_NAME: "push",
        GITHUB_SHA: head,
        GITHUB_EVENT_PATH: event,
        GITHUB_OUTPUT: output,
        GITHUB_STEP_SUMMARY: summary,
      });
      assert.equal(result.status, 0, result.stderr);
      assert.equal(readFileSync(output, "utf8"), `groups=${JSON.stringify(expected)}\n`);
      assert.match(readFileSync(summary, "utf8"), /Mutation groups:/);
    }
    writeFileSync(event, JSON.stringify({ before: "f".repeat(40) }));
    const invalid = controller(["select"], {
      ...env,
      GITHUB_EVENT_NAME: "push",
      GITHUB_SHA: head,
      GITHUB_EVENT_PATH: event,
    });
    assert.equal(invalid.status, 1);
    assert.match(invalid.stderr, /Unable to compare/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("controller rejects bad commands and nonarray selections", () => {
  for (const [args, env, error] of [
    [[], {}, /Use select or run/],
    [["run"], { MUTATION_GROUPS: "{}" }, /must be an array/],
    [["run"], { MUTATION_GROUPS: "not JSON" }, /JSON/],
  ]) {
    const result = controller(args, env);
    assert.equal(result.status, 1);
    assert.match(result.stderr, error);
  }
});
