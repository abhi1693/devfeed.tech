import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";
import { affectsBrowser, changedPaths } from "./browser-performance-changes.mjs";
import { lighthouseConfig, runs } from "./browser-performance-config.mjs";
import { checkMeasurements, measurementSummary } from "./browser-performance-report.mjs";

const require = createRequire(import.meta.url);
const { getAllAssertionResults } = require("@lhci/utils/src/assertions.js");
const url = "http://127.0.0.1:3000/latest";
const config = lighthouseConfig("web", [url], "/usr/bin/chromium", "/tmp/reports");
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

test("frontend consumers, assets, dependencies and the gate trigger browser budgets", () => {
  for (const path of [
    "apps/web/src/app/latest/page.tsx",
    "apps/web/public/opengraph.png",
    "apps/admin/src/app/page.tsx",
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
    affectsBrowser(["README.md", "packages/core/src/models.py", "apps/api/main.py"]),
    false,
  );
});

test("missing and unsafe comparison refs fail closed", () => {
  for (const base of [undefined, "", "--output=/tmp/report", "master", "$(id)"])
    assert.throws(() => changedPaths(base, "a".repeat(40)), /valid base and head commits/);
});

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

test("Lighthouse assertions pass the baseline and fail each timing and asset regression", () => {
  const reports = Array.from({ length: runs }, sample);
  assert.ok(getAllAssertionResults(config.ci.assert, reports).every((result) => result.passed));
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
        (result) => assertionKey(result) === auditId && !result.passed && result.level === "error",
      ),
      auditId,
    );
  }
});

test("a timing outlier uses the median, but a single oversized script fails", () => {
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
  const summary = measurementSummary("web", reports, [url], results);
  assert.match(summary, /1\.80 s/);
  assert.match(summary, /20480 KiB/);
  assert.match(summary, /resource-summary:script:size/);
});

test("existing rendering debt warns while the regression ceiling still passes", () => {
  const reports = Array.from({ length: runs }, sample);
  for (const report of reports) {
    report.audits["largest-contentful-paint"].numericValue = 3000;
    report.audits["total-blocking-time"].numericValue = 250;
  }
  const results = getAllAssertionResults(config.ci.assert, reports);
  assert.ok(results.filter((result) => result.level === "error").every((result) => result.passed));
  assert.equal(results.filter((result) => result.level === "warn" && !result.passed).length, 2);
  assert.match(measurementSummary("web", reports, [url], results), /warn: \/latest/);
});

test("each route is budgeted and the article allowance does not weaken the feed limit", () => {
  const articleUrl = "http://127.0.0.1:3000/articles/example";
  const urls = [url, articleUrl];
  const options = lighthouseConfig("web", urls, "/usr/bin/chromium", "/tmp/reports");
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
    (result) => result.auditId === "largest-contentful-paint" && result.level === "error",
  );
  assert.equal(lcp.find((result) => result.url === url).passed, false);
  assert.equal(lcp.find((result) => result.url === articleUrl).passed, true);
});
