import { appendFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
export const policy = JSON.parse(
  readFileSync(new URL("./mutation-policy.json", import.meta.url), "utf8"),
);
const groups = Object.keys(policy);

export function suiteCoverage(result) {
  if (!result.mutantCoverage) return result;
  const coverage = { ...result.mutantCoverage.static };
  for (const tests of Object.values(result.mutantCoverage.perTest))
    for (const [id, hits] of Object.entries(tests)) coverage[id] = (coverage[id] || 0) + hits;
  return { ...result, mutantCoverage: { static: coverage, perTest: {} } };
}

export function selectGroups(files) {
  const selected = new Set();
  for (const file of files) {
    const owners = groups.filter((group) => Object.hasOwn(policy[group].files, file));
    if (owners.length) owners.forEach((group) => selected.add(group));
    else if (
      ["package.json", "package-lock.json"].includes(file) ||
      file.startsWith("apps/web/") ||
      file.startsWith("packages/") ||
      file.startsWith("scripts/ci/mutation") ||
      file === "scripts/ci/stryker.config.mjs" ||
      file === "tests/mutation.test.mjs" ||
      [".github/workflows/ci.yml", ".github/workflows/mutation.yml"].includes(file)
    )
      groups.forEach((group) => selected.add(group));
  }
  return groups.filter((group) => selected.has(group));
}

export function comparison(eventName, event, head) {
  if (["schedule", "workflow_dispatch"].includes(eventName)) return null;
  const base =
    eventName === "pull_request"
      ? event.pull_request?.base?.sha
      : eventName === "merge_group"
        ? event.merge_group?.base_sha
        : eventName === "push"
          ? event.before
          : undefined;
  for (const sha of [base, head])
    if (typeof sha !== "string" || !/^(?:[a-f\d]{40}|[a-f\d]{64})$/i.test(sha))
      throw new Error("Mutation selection requires valid base and head commits");
  return `${base}${eventName === "push" ? ".." : "..."}${head}`;
}

export function checkReport(report, group) {
  const expected = policy[group]?.files;
  if (!expected || !report?.files || typeof report.files !== "object")
    throw new Error("Missing mutation files");
  const sorted = (files) =>
    Object.keys(files)
      .sort((a, b) => a.localeCompare(b))
      .join("\n");
  if (sorted(report.files) !== sorted(expected))
    throw new Error("Mutation report does not match configured files");
  return Object.entries(expected).map(([file, minimum]) => {
    if (!Number.isFinite(minimum) || minimum <= 0 || minimum > 100)
      throw new Error(`Invalid mutation minimum for ${file}`);
    const mutants = report.files[file].mutants;
    if (!Array.isArray(mutants) || !mutants.length) throw new Error(`No mutants for ${file}`);
    const counts = { Killed: 0, Timeout: 0, Survived: 0, NoCoverage: 0, CompileError: 0 };
    const ids = new Set();
    for (const mutant of mutants) {
      if (
        typeof mutant.id !== "string" ||
        ids.has(mutant.id) ||
        !Object.hasOwn(counts, mutant.status)
      )
        throw new Error(`Incomplete or invalid mutation result for ${file}`);
      ids.add(mutant.id);
      if (mutant.status === "Survived" && !(mutant.testsCompleted > 0))
        throw new Error(`Surviving mutant ran no tests for ${file}`);
      counts[mutant.status] += 1;
    }
    const total = mutants.length - counts.CompileError;
    if (!total) throw new Error(`No valid mutants for ${file}`);
    const score = (100 * (counts.Killed + counts.Timeout)) / total;
    return { file, score, minimum, total, ...counts, passed: score >= minimum };
  });
}

function summary(text) {
  console.log(text);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, text + "\n");
}

function select() {
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"));
  const range = comparison(process.env.GITHUB_EVENT_NAME, event, process.env.GITHUB_SHA);
  let selected = groups;
  if (range) {
    const diff = spawnSync("/usr/bin/git", ["diff", "--name-only", "-z", range, "--"], {
      cwd: root,
      encoding: "utf8",
      timeout: 30_000,
    });
    if (diff.error || diff.status !== 0) throw new Error("Unable to compare mutation changes");
    selected = selectGroups(diff.stdout.split("\0").filter(Boolean));
  }
  if (process.env.GITHUB_OUTPUT)
    appendFileSync(process.env.GITHUB_OUTPUT, `groups=${JSON.stringify(selected)}\n`);
  summary(`Mutation groups: ${selected.join(", ") || "none (no relevant changes)"}.`);
}

export async function runCommand(command, args, timeoutMs, env = process.env) {
  const child = spawn(command, args, {
    cwd: root,
    env,
    stdio: "inherit",
    detached: process.platform !== "win32",
  });
  let expired = false;
  const timer = setTimeout(() => {
    expired = true;
    // Stop Stryker and every test worker together when the group exceeds its budget.
    if (process.platform === "win32") child.kill("SIGKILL");
    else if (child.pid) {
      try {
        process.kill(-child.pid, "SIGKILL");
      } catch (error) {
        if (error.code !== "ESRCH") child.kill("SIGKILL");
      }
    }
  }, timeoutMs);
  try {
    return await new Promise((resolve) => {
      child.once("error", (error) => resolve({ error }));
      child.once("close", (status, signal) => resolve({ status, signal, expired }));
    });
  } finally {
    clearTimeout(timer);
  }
}

export async function run(selected) {
  if (!selected.length || selected.some((group) => !groups.includes(group)))
    throw new Error("Select configured mutation groups");
  const results = [];
  let failed = false;
  for (const group of new Set(selected)) {
    const directory = path.join(root, "reports/mutation", group);
    rmSync(directory, { recursive: true, force: true });
    mkdirSync(directory, { recursive: true });
    const env = { ...process.env, MUTATION_GROUP: group };
    // Coverage measures this controller and its tests, not instrumented mutants or workers.
    delete env.NODE_V8_COVERAGE;
    const execution = await runCommand(
      process.execPath,
      ["node_modules/@stryker-mutator/core/bin/stryker.js", "run", "scripts/ci/stryker.config.mjs"],
      12 * 60_000,
      env,
    );
    try {
      if (execution.error || execution.signal) throw new Error("Mutation runner did not complete");
      const files = checkReport(
        JSON.parse(readFileSync(path.join(directory, "mutation.json"))),
        group,
      );
      const passed = execution.status === 0 && files.every((file) => file.passed);
      failed ||= !passed;
      results.push({ group, passed, files });
    } catch (error) {
      failed = true;
      results.push({ group, passed: false, error: error.message });
    }
  }
  const lines = [
    "## Mutation tests",
    "",
    `Commit: ${process.env.GITHUB_SHA || "local"}`,
    "",
    "| File | Score | Minimum | Survived | Uncovered | Result |",
    "| --- | ---: | ---: | ---: | ---: | --- |",
  ];
  for (const result of results) {
    if (result.error) lines.push(`| ${result.group} | - | - | - | - | ${result.error} |`);
    else
      for (const file of result.files)
        lines.push(
          `| ${file.file} | ${file.score.toFixed(2)}% | ${file.minimum}% | ${file.Survived} | ${file.NoCoverage} | ${file.passed && result.passed ? "Pass" : "Fail"} |`,
        );
  }
  lines.push(
    "",
    "HTML reports show surviving mutations. Invalid compile mutations do not count toward scores.",
  );
  const directory = path.join(root, "reports/mutation");
  mkdirSync(directory, { recursive: true });
  writeFileSync(path.join(directory, "summary.json"), JSON.stringify(results, null, 2) + "\n");
  writeFileSync(path.join(directory, "summary.md"), lines.join("\n") + "\n");
  summary(lines.join("\n"));
  if (failed) process.exitCode = 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv[2] === "select") select();
    else if (process.argv[2] === "run") {
      const selected = process.env.MUTATION_GROUPS
        ? JSON.parse(process.env.MUTATION_GROUPS)
        : process.argv.slice(3);
      if (!Array.isArray(selected)) throw new Error("Mutation groups must be an array");
      await run(selected.includes("all") ? groups : selected);
    } else throw new Error("Use select or run <group...|all>");
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
