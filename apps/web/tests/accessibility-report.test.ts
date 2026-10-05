import { afterEach, expect, it } from "vitest";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { writeAccessibilitySummary } from "../../../scripts/ci/accessibility-report.mjs";

const directories: string[] = [];
afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true })));
});

async function fixture() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "devfeed-accessibility-report-"));
  directories.push(directory);
  const report = {
    status: "passed",
    scans: [{ violations: [], incomplete: 1, passed: 20 }],
    interactions: [{ name: "keyboard", status: "passed" }],
  };
  for (const surface of ["web", "chrome", "edge"]) {
    for (const state of ["guest", "signed-in"]) {
      const destination = path.join(directory, surface, state);
      await mkdir(destination, { recursive: true });
      await writeFile(path.join(destination, "summary.json"), JSON.stringify(report));
    }
  }
  return { directory, report, file: path.join(directory, "edge/signed-in/summary.json") };
}

it("summarizes every session and retains incomplete checks for manual review", async () => {
  const { directory } = await fixture();
  const stepSummary = path.join(directory, "github-summary.md");
  const result = await writeAccessibilitySummary(directory, stepSummary);
  expect(result.failed).toBe(false);
  expect(result.summary.match(/PASS/g)).toHaveLength(6);
  expect(result.summary).toContain("| edge | signed-in | PASS | 1 | 1 | 0 | 1 |");
  expect(result.summary).toContain("manual review");
  expect(await readFile(stepSummary, "utf8")).toBe(result.summary);
  expect(await readFile(path.join(directory, "summary.md"), "utf8")).toBe(result.summary);
});

it("fails when any runtime report is missing or corrupt", async () => {
  const { directory, file } = await fixture();
  await rm(file);
  expect(await writeAccessibilitySummary(directory)).toMatchObject({ failed: true });
  await writeFile(file, "invalid JSON");
  const result = await writeAccessibilitySummary(directory);
  expect(result.failed).toBe(true);
  expect(result.summary).toContain("| edge | signed-in | MISSING |");
});

it("rejects partial, empty, violated or failed-interaction results", async () => {
  const { directory, file, report } = await fixture();
  for (const invalid of [
    { ...report, status: "running" },
    { ...report, status: "failed" },
    { ...report, scans: [] },
    { ...report, interactions: [] },
    { ...report, scans: [{ violations: [{ id: "button-name" }], incomplete: 0 }] },
    { ...report, interactions: [{ name: "keyboard", status: "failed" }] },
  ]) {
    await writeFile(file, JSON.stringify(invalid));
    const result = await writeAccessibilitySummary(directory);
    expect(result.failed).toBe(true);
    expect(result.summary).toContain("| edge | signed-in | FAIL |");
  }
});
