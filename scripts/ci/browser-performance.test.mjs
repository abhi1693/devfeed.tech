import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { test } from "node:test";
import { affectsBrowser, changedPaths } from "./browser-performance-changes.mjs";
import { lighthouseConfig, readerPaths, runs } from "./browser-performance-config.mjs";
import { checkMeasurements, measurementSummary } from "./browser-performance-report.mjs";

const require = createRequire(import.meta.url);
const { getAllAssertionResults } = require("@lhci/utils/src/assertions.js");
const url = "http://127.0.0.1:3000/latest";
const reportsDirectory = fileURLToPath(
  new URL("../../reports/browser-performance/tests/", import.meta.url),
);
const config = lighthouseConfig([url], "/usr/bin/chromium", reportsDirectory);
const assertionKey = (result) =>
  result.auditId + (result.auditProperty ? `:${result.auditProperty.replaceAll(".", ":")}` : "");
const sample = () => ({
  requestedUrl: url,
  finalUrl: url,
  finalDisplayedUrl: url,
  audits: {
    "network-requests": {
      details: { items: [{ resourceType: "Document", url, statusCode: 200 }] },
    },
    "errors-in-console": { score: 1 },
    "first-contentful-paint": { numericValue: 1200 },
    "largest-contentful-paint": { numericValue: 1800 },
    "cumulative-layout-shift": { numericValue: 0.01 },
    "total-blocking-time": { numericValue: 100 },
    "resource-summary": {
      details: {
        items: ["script", "stylesheet", "image", "total"].map((resourceType) => ({
          resourceType,
          transferSize: 1000,
          requestCount: 1,
        })),
      },
    },
  },
});

test("Lighthouse dependency upgrades preserve CLI options and YAML parsing", () => {
  const cliRequire = createRequire(require.resolve("@lhci/cli/package.json"));
  const parser = cliRequire("yargs-parser");
  const options = parser([
    "--config",
    "budgets.json",
    "--numberOfRuns",
    "3",
    "--foo.__proto__.polluted",
    "true",
  ]);
  assert.equal(options.config, "budgets.json");
  assert.equal(options.numberOfRuns, 3);
  assert.equal(Object.prototype.polluted, undefined);
  const utilsRequire = createRequire(require.resolve("@lhci/utils/package.json"));
  const yaml = "ci:\n  collect:\n    numberOfRuns: 3\n";
  const expected = { ci: { collect: { numberOfRuns: 3 } } };
  assert.deepEqual(utilsRequire("js-yaml").safeLoad(yaml), expected);
  const converted = execFileSync(
    process.execPath,
    [utilsRequire.resolve("js-yaml/bin/js-yaml.js")],
    {
      input: yaml,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
      timeout: 30_000,
    },
  );
  assert.deepEqual(JSON.parse(converted), expected);
});

test("all six requested reader pages are measured and admin stays outside the suite", () => {
  const paths = readerPaths("example-article");
  assert.deepEqual(paths, [
    "/latest",
    "/articles/example-article",
    "/topics",
    "/sources",
    "/users/budget-reader",
    "/leaderboard",
  ]);
  const urls = paths.map((path) => `http://127.0.0.1:3000${path}`);
  const measurements = urls.flatMap((requestedUrl) =>
    Array.from({ length: runs }, () => ({
      ...sample(),
      requestedUrl,
      finalUrl: requestedUrl,
      finalDisplayedUrl: requestedUrl,
      audits: {
        ...sample().audits,
        "network-requests": {
          details: { items: [{ resourceType: "Document", url: requestedUrl, statusCode: 200 }] },
        },
      },
    })),
  );
  checkMeasurements(measurements, urls, runs);
  assert.throws(() => checkMeasurements(measurements.slice(1), urls, runs), /Missing/);
  const assertions = getAllAssertionResults(
    lighthouseConfig(urls, "/usr/bin/chromium", reportsDirectory).ci.assert,
    measurements,
  );
  const summary = measurementSummary(measurements, urls, assertions);
  for (const name of [
    "Latest",
    "Article preview",
    "Topics",
    "Sources",
    "Public profile",
    "Leaderboard",
  ])
    assert.ok(summary.includes(`| ${name} (`), name);
  assert.equal(assertions.length, 60);
  assert.ok(assertions.every((assertion) => assertion.passed));
});

test("frontend consumers, assets, dependencies and the gate trigger browser budgets", () => {
  for (const path of [
    "apps/web/src/app/latest/page.tsx",
    "apps/web/public/opengraph.png",
    "apps/extensions/build.mjs",
    "packages/ui/src/index.ts",
    "packages/theme/assets/devfeed-icon-32.png",
    "packages/telemetry/src/faro.ts",
    "package.json",
    "package-lock.json",
    ".github/workflows/ci.yml",
    "scripts/ci/browser-performance-config.mjs",
    "scripts/testing/article-grid.mjs",
  ])
    assert.equal(affectsBrowser([path]), true, path);
  assert.equal(
    affectsBrowser([
      "README.md",
      "packages/core/src/models.py",
      "apps/api/main.py",
      "apps/admin/src/app/page.tsx",
    ]),
    false,
  );
});

test("missing and unsafe comparison refs fail closed", () => {
  for (const base of [undefined, "", "--output=/tmp/report", "master", "$(id)"])
    assert.throws(() => changedPaths(base, "a".repeat(40)), /valid base and head commits/);
});

test("comparison uses the system Git independently of PATH", () => {
  const head = execFileSync("/usr/bin/git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  const module = new URL("./browser-performance-changes.mjs", import.meta.url).href;
  const code = `import { changedPaths } from ${JSON.stringify(module)};
    process.stdout.write(JSON.stringify(changedPaths(${JSON.stringify(head)}, ${JSON.stringify(head)})));`;
  const result = execFileSync(process.execPath, ["--input-type=module", "-e", code], {
    env: { ...process.env, PATH: "" },
    encoding: "utf8",
    timeout: 30_000,
  });
  assert.equal(result, "[]");
});

for (const needed of [false, true]) {
  test(`the selector CLI reports frontend changes: ${needed}`, () => {
    const head = execFileSync("/usr/bin/git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    const lastPackageChange = execFileSync(
      "/usr/bin/git",
      ["log", "-1", "--format=%H", "--", "package.json"],
      { encoding: "utf8" },
    ).trim();
    const base = needed
      ? execFileSync("/usr/bin/git", ["rev-parse", `${lastPackageChange}^`], {
          encoding: "utf8",
        }).trim()
      : head;
    const directory = mkdtempSync(join(tmpdir(), "devfeed-budget-selection-"));
    const event = join(directory, "event.json");
    const output = join(directory, "output.txt");
    const summary = join(directory, "summary.md");
    writeFileSync(event, JSON.stringify({ pull_request: { base: { sha: base } } }));
    writeFileSync(summary, "");
    try {
      execFileSync(
        process.execPath,
        [fileURLToPath(new URL("./browser-performance-changes.mjs", import.meta.url))],
        {
          env: {
            ...process.env,
            PATH: "",
            GITHUB_EVENT_PATH: event,
            GITHUB_OUTPUT: output,
            GITHUB_STEP_SUMMARY: summary,
          },
          timeout: 30_000,
        },
      );
      assert.equal(readFileSync(output, "utf8"), `needed=${needed}\n`);
      assert.equal(
        readFileSync(summary, "utf8"),
        needed ? "" : "Browser budgets: no reader changes.\n",
      );
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
}

test("every requested page needs three complete, successful, unredirected measurements", () => {
  const reports = Array.from({ length: runs }, sample);
  checkMeasurements(reports, [url], runs);
  assert.throws(() => checkMeasurements([], [url], runs), /Missing Lighthouse/);
  assert.throws(() => checkMeasurements(reports.slice(1), [url], runs), /Missing Lighthouse/);
  for (const corrupt of [
    (report) => (report.requestedUrl = "http://127.0.0.1:3000/login"),
    (report) => (report.finalDisplayedUrl = "http://127.0.0.1:3000/login"),
    (report) => (report.runtimeError = { code: "NO_FCP" }),
    (report) => (report.runWarnings = ["The page loaded too slowly. Results may be incomplete."]),
    (report) => (report.audits["network-requests"].details.items[0].statusCode = 500),
    (report) => (report.audits["errors-in-console"].score = 0),
    (report) => delete report.audits["largest-contentful-paint"],
    (report) => (report.audits["resource-summary"].details.items = []),
  ]) {
    const broken = structuredClone(reports);
    corrupt(broken[0]);
    assert.throws(() => checkMeasurements(broken, [url], runs));
  }
});

test("Lighthouse reports each timing and asset regression as an advisory warning", () => {
  const reports = Array.from({ length: runs }, sample);
  assert.ok(getAllAssertionResults(config.ci.assert, reports).every((result) => result.passed));
  for (const row of config.ci.assert.assertMatrix)
    for (const [level] of Object.values(row.assertions)) assert.equal(level, "warn");
  for (const [auditId, [, options]] of Object.entries(
    config.ci.assert.assertMatrix[0].assertions,
  )) {
    const broken = structuredClone(reports);
    for (const report of broken) {
      if (auditId.startsWith("resource-summary:")) {
        const type = auditId.split(":")[1];
        report.audits["resource-summary"].details.items.find(
          (item) => item.resourceType === type,
        ).transferSize = options.maxNumericValue + 1;
      } else report.audits[auditId].numericValue = options.maxNumericValue + 1;
    }
    const results = getAllAssertionResults(config.ci.assert, broken);
    assert.ok(
      results.some(
        (result) => assertionKey(result) === auditId && !result.passed && result.level === "warn",
      ),
      auditId,
    );
  }
});

test("a timing outlier uses the median and a single oversized script warns", () => {
  const reports = Array.from({ length: runs }, sample);
  reports[0].audits["largest-contentful-paint"].numericValue = 20000;
  reports[0].audits["resource-summary"].details.items[0].transferSize = 20 * 1024 * 1024;
  const results = getAllAssertionResults(config.ci.assert, reports);
  assert.equal(
    results.find((result) => result.auditId === "largest-contentful-paint").passed,
    true,
  );
  assert.equal(
    results.find((result) => assertionKey(result) === "resource-summary:script:size").passed,
    false,
  );
  const summary = measurementSummary(reports, [url], results);
  assert.match(summary, /1\.80 s/);
  assert.match(summary, /20480 KiB/);
  assert.match(summary, /JS transfer/);
});

test("existing rendering debt keeps the tighter targets visible", () => {
  const reports = Array.from({ length: runs }, sample);
  for (const report of reports) {
    report.audits["largest-contentful-paint"].numericValue = 3000;
    report.audits["total-blocking-time"].numericValue = 250;
  }
  const results = getAllAssertionResults(config.ci.assert, reports);
  assert.ok(results.every((result) => result.level === "warn"));
  assert.equal(results.filter((result) => result.level === "warn" && !result.passed).length, 2);
  const summary = measurementSummary(reports, [url], results);
  assert.match(summary, /2 target warnings/);
  assert.match(summary, /\*\*3\.00 s\*\*/);
  assert.match(summary, /\*\*250 ms\*\*/);
  assert.match(summary, /⚠️ Target · LCP \| 3\.00 s \| 2\.50 s/);
  const clean = Array.from({ length: runs }, sample);
  const cleanSummary = measurementSummary(
    clean,
    [url],
    getAllAssertionResults(config.ci.assert, clean),
  );
  assert.ok(cleanSummary.startsWith("| Page | FCP |"));
  assert.doesNotMatch(cleanSummary, /Targets met|advisory|warnings/);
});

test("each route is budgeted and the article allowance does not weaken the feed limit", () => {
  const articleUrl = "http://127.0.0.1:3000/articles/example";
  const urls = [url, articleUrl];
  const options = lighthouseConfig(urls, "/usr/bin/chromium", reportsDirectory);
  const reports = urls.flatMap((target) =>
    Array.from({ length: runs }, () => ({
      ...sample(),
      requestedUrl: target,
      finalUrl: target,
      finalDisplayedUrl: target,
    })),
  );
  for (const report of reports) report.audits["largest-contentful-paint"].numericValue = 4600;
  const results = getAllAssertionResults(options.ci.assert, reports);
  assert.equal(results.length, 20);
  const lcp = results.filter(
    (result) => result.auditId === "largest-contentful-paint" && result.expected > 2500,
  );
  assert.equal(lcp.find((result) => result.url === url).passed, false);
  assert.equal(lcp.find((result) => result.url === articleUrl).passed, true);
});

test("real Lighthouse CLI exits successfully even when every numeric limit is exceeded", () => {
  const directory = mkdtempSync(join(tmpdir(), "devfeed-advisory-budgets-"));
  try {
    const options = lighthouseConfig([url], "/usr/bin/chromium", directory);
    for (const row of options.ci.assert.assertMatrix)
      for (const [, assertion] of Object.values(row.assertions)) assertion.maxNumericValue = 0;
    const configPath = join(directory, "config.json");
    writeFileSync(configPath, JSON.stringify(options));
    const reports = Array.from({ length: runs }, sample);
    const results = getAllAssertionResults(options.ci.assert, reports);
    assert.ok(results.every((result) => !result.passed && result.level === "warn"));
    const input = join(directory, "measurement.json");
    writeFileSync(input, JSON.stringify(sample()));
    execFileSync(
      process.execPath,
      [
        require.resolve("@lhci/cli/src/cli.js"),
        "assert",
        `--config=${configPath}`,
        `--lhr=${input}`,
      ],
      { cwd: directory, encoding: "utf8", timeout: 30_000, stdio: ["ignore", "pipe", "pipe"] },
    );
    const actual = JSON.parse(
      readFileSync(join(directory, ".lighthouseci/assertion-results.json"), "utf8"),
    );
    assert.equal(actual.length, 10);
    assert.ok(actual.every((result) => !result.passed && result.level === "warn"));
    const summary = measurementSummary(reports, [url], results);
    assert.doesNotMatch(summary, /Targets met|limits are advisory/);
    assert.match(summary, /10 target warnings/);
    assert.doesNotMatch(summary, /block CI/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
