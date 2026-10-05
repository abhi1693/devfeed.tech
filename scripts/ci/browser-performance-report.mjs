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

const names = {
  "first-contentful-paint": "FCP",
  "largest-contentful-paint": "LCP",
  "cumulative-layout-shift": "CLS",
  "total-blocking-time": "TBT",
  "resource-summary:script.size": "JS transfer",
  "resource-summary:total.size": "Total transfer",
  "resource-summary:stylesheet.size": "CSS transfer",
  "resource-summary:image.size": "Images",
};

function pageName(url) {
  const pathname = new URL(url).pathname;
  if (pathname.startsWith("/articles/")) return "Article preview (`/articles/…`)";
  if (pathname.startsWith("/users/")) return "Public profile (`/users/…`)";
  const name = {
    "/latest": "Latest",
    "/topics": "Topics",
    "/sources": "Sources",
    "/leaderboard": "Leaderboard",
  }[pathname];
  return `${name ?? "Reader"} (\`${pathname}\`)`;
}

function formatValue(metric, value) {
  if (metric === "cumulative-layout-shift") return value.toFixed(3);
  if (metric === "total-blocking-time") return `${Math.round(value)} ms`;
  if (metric.startsWith("resource-summary:")) return `${Math.ceil(value / 1024)} KiB`;
  return `${(value / 1000).toFixed(2)} s`;
}

export function measurementSummary(reports, urls, assertions) {
  const findings = assertions.filter((result) => !result.passed);
  const warnings = findings.filter((result) => result.level === "warn");
  const lines = [
    warnings.length
      ? `**⚠️ ${warnings.length} target ${warnings.length === 1 ? "warning" : "warnings"}** · Bold values need attention. Hard budget failures block CI.`
      : "**No target warnings.** Hard budget failures block CI.",
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
    const highlight = (metric, value) => {
      const formatted = formatValue(metric, value);
      return findings.some(
        (finding) =>
          finding.url === url &&
          finding.auditId + (finding.auditProperty ? `:${finding.auditProperty}` : "") === metric,
      )
        ? `**${formatted}**`
        : formatted;
    };
    const cells = metrics.map((metric, index) => highlight(metric, values[index]));
    cells.push(
      highlight("resource-summary:script.size", bytes("script")),
      highlight("resource-summary:total.size", bytes("total")),
    );
    lines.push(`| ${pageName(url)} | ${cells.join(" | ")} |`);
  }
  lines.push(
    "",
    "Mobile reader · Three cold loads per page · Median timings · Largest transfer size.",
    "",
  );
  if (findings.length) {
    lines.push(
      "**Needs attention**",
      "",
      "| Page | Finding | Measured | Target / limit |",
      "| --- | --- | ---: | ---: |",
    );
    for (const finding of findings) {
      const metric = finding.auditId + (finding.auditProperty ? `:${finding.auditProperty}` : "");
      const label = names[metric] ?? metric.replaceAll(".", ":");
      lines.push(
        `| ${pageName(finding.url)} | ${finding.level === "warn" ? "⚠️ Target" : "❌ Budget"} · ${label} | ${formatValue(metric, finding.actual)} | ${formatValue(metric, finding.expected)} |`,
      );
    }
    lines.push("");
  }
  return `${lines.join("\n")}\n`;
}
