import assert from "node:assert/strict";

const metrics = [
  "first-contentful-paint",
  "largest-contentful-paint",
  "cumulative-layout-shift",
  "total-blocking-time",
];

export function checkMeasurements(reports, urls, runs) {
  assert.equal(reports.length, urls.length * runs, "Missing Lighthouse measurements");
  for (const url of urls) {
    const samples = reports.filter((report) => report.requestedUrl === url);
    assert.equal(samples.length, runs, `Expected ${runs} measurements for ${url}`);
    for (const report of samples) {
      assert.equal(report.runtimeError, undefined, `Lighthouse failed for ${url}`);
      assert.ok(
        !(report.runWarnings ?? []).some((warning) => /too slowly|incomplete/i.test(warning)),
        "Lighthouse did not finish loading the page",
      );
      assert.equal(report.finalDisplayedUrl ?? report.finalUrl, url, "Unexpected redirect");
      const document = report.audits["network-requests"]?.details?.items.find(
        (item) => item.resourceType === "Document" && item.url === url,
      );
      assert.equal(document?.statusCode, 200, "Page returned an error");
      assert.equal(report.audits["errors-in-console"]?.score, 1, "Browser console errors");
      for (const metric of metrics)
        assert.ok(
          Number.isFinite(report.audits[metric]?.numericValue) &&
            report.audits[metric].numericValue >= 0,
          `Missing ${metric} for ${url}`,
        );
      const resources = report.audits["resource-summary"]?.details?.items;
      assert.ok(resources?.some((item) => item.resourceType === "script" && item.transferSize > 0));
      assert.ok(resources?.some((item) => item.resourceType === "total" && item.transferSize > 0));
    }
  }
}

const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

export function measurementSummary(app, reports, urls, assertions) {
  const lines = [
    `### ${app === "web" ? "Reader (mobile)" : "Admin (desktop)"}`,
    "",
    "| Page | FCP | LCP | CLS | TBT | JS | Total |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const url of urls) {
    const samples = reports.filter((report) => report.requestedUrl === url);
    const values = metrics.map((metric) =>
      median(samples.map((report) => report.audits[metric].numericValue)),
    );
    const bytes = (type) =>
      Math.max(
        ...samples.map(
          (report) =>
            report.audits["resource-summary"].details.items.find(
              (item) => item.resourceType === type,
            ).transferSize,
        ),
      );
    lines.push(
      `| ${new URL(url).pathname} | ${(values[0] / 1000).toFixed(2)} s | ${(values[1] / 1000).toFixed(2)} s | ${values[2].toFixed(3)} | ${Math.round(values[3])} ms | ${Math.ceil(bytes("script") / 1024)} KiB | ${Math.ceil(bytes("total") / 1024)} KiB |`,
    );
  }
  lines.push(
    "",
    "Timings are medians of three cold loads; transfer sizes are the largest run.",
    "",
  );
  for (const assertion of assertions.filter((result) => !result.passed))
    lines.push(
      `- ${assertion.level}: ${new URL(assertion.url).pathname} / ${assertion.auditId}${assertion.auditProperty ? `:${assertion.auditProperty.replaceAll(".", ":")}` : ""}: ${assertion.actual} (limit ${assertion.expected})`,
    );
  return `${lines.join("\n")}\n`;
}
