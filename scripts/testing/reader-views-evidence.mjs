import { execFileSync } from "node:child_process";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const runtimes = new Set(["web", "chrome", "edge"]);
export const savedReaderViewScenarios = Object.freeze([
  "account-lifecycle",
  "save-recovery",
  "accessibility-guest",
  "accessibility-member",
  "column-budgets",
]);
const scenarios = new Set(savedReaderViewScenarios);

/** The existing reader CI artifact already collects this ignored reports directory. */
export function readerViewsReportDirectory(runtime, scenario) {
  if (!runtimes.has(runtime) || !scenarios.has(scenario))
    throw new TypeError("Unsupported saved reader view evidence context");
  const root = process.env.DEVFEED_READER_VIEWS_REPORT_DIR;
  return path.resolve(root || path.join(repository, "reports/reader-views"), runtime, scenario);
}

function sourceIdentity() {
  const git = (...args) =>
    execFileSync("git", args, {
      cwd: repository,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  const revision = git("rev-parse", "HEAD");
  if (process.env.GITHUB_SHA && process.env.GITHUB_SHA !== revision)
    throw new Error("Saved reader view evidence does not match the checked-out CI revision");
  const attempt = process.env.GITHUB_RUN_ATTEMPT ?? "local";
  if (attempt !== "local" && !/^[1-9]\d*$/.test(attempt))
    throw new TypeError("Unsupported CI run attempt");
  return {
    source_revision: revision,
    source_dirty: git("status", "--porcelain=v1", "--untracked-files=normal") !== "",
    run_attempt: attempt,
  };
}

/** Record observed results, including failures, without overwriting another runtime's evidence. */
export async function writeReaderViewReport(directory, proof) {
  const destination = path.resolve(directory);
  const scenario = path.basename(destination);
  const runtime = path.basename(path.dirname(destination));
  if (destination !== readerViewsReportDirectory(runtime, scenario))
    throw new TypeError("Saved reader view evidence must use its configured report directory");
  if (!proof || !["running", "passed", "failed"].includes(proof.status))
    throw new TypeError("Saved reader view evidence must declare its observed status");
  const report = {
    ...proof,
    schema_version: 1,
    runtime,
    scenario,
    ...sourceIdentity(),
    recorded_at: new Date().toISOString(),
  };
  await mkdir(destination, { recursive: true });
  const temporary = path.join(destination, `.summary-${process.pid}.tmp`);
  try {
    await writeFile(temporary, `${JSON.stringify(report, null, 2)}\n`);
    await rename(temporary, path.join(destination, "summary.json"));
  } finally {
    await rm(temporary, { force: true });
  }
  return report;
}

/** Preserve the outcome even when a browser assertion aborts before its own final report. */
export async function runReaderViewCheck(runtime, scenario, check) {
  const directory = readerViewsReportDirectory(runtime, scenario);
  await writeReaderViewReport(directory, { status: "running" });
  try {
    const observed = await check(directory);
    return await writeReaderViewReport(directory, { ...observed, status: "passed" });
  } catch (error) {
    await writeReaderViewReport(directory, { status: "failed", error: error.message });
    throw error;
  }
}
