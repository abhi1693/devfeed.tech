import assert from "node:assert/strict";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { chromium } from "playwright";
import { readerPaths } from "../ci/browser-performance-config.mjs";

// Paired production-build measurements with identical fixtures and fresh anonymous browsers.
export async function auditRouteStyles({ origin, articleSlug, output, phase }) {
  const { default: lighthouse } = await import("lighthouse");
  const { default: desktopConfig } = await import("lighthouse/core/config/desktop-config.js");
  const { launch } = await import("chrome-launcher");
  const measurements = [];
  for (const desktop of [false, true]) {
    const profile = desktop ? "desktop" : "mobile";
    const viewport = desktop ? { width: 1350, height: 940 } : { width: 412, height: 823 };
    const browser = await chromium.launch();
    try {
      for (const path of readerPaths(articleSlug)) {
        const context = await browser.newContext({
          viewport,
          deviceScaleFactor: desktop ? 1 : 1.75,
          isMobile: !desktop,
        });
        const page = await context.newPage();
        await page.coverage.startCSSCoverage();
        await page.goto(origin + path);
        await page.waitForLoadState("networkidle");
        const coverage = await page.coverage.stopCSSCoverage();
        const sheets = coverage.map(({ url, text, ranges }) => ({
          url: new URL(url).pathname,
          bytes: Buffer.byteLength(text),
          usedBytes: ranges.reduce(
            (sum, range) => sum + Buffer.byteLength(text.slice(range.start, range.end)),
            0,
          ),
        }));
        const name = `${profile}-${path.replaceAll("/", "_")}`;
        await page.screenshot({
          path: `${output}/${name}.png`,
          fullPage: true,
          animations: "disabled",
        });
        measurements.push({ profile, path, sheets });
        await context.close();
      }
    } finally {
      await browser.close();
    }
    for (const path of ["/latest", "/topics", "/leaderboard"]) {
      for (let run = 0; run < 3; run++) {
        const chrome = await launch({
          chromePath: chromium.executablePath(),
          chromeFlags: ["--headless", "--no-sandbox"],
        });
        try {
          const result = await lighthouse(
            origin + path,
            {
              port: chrome.port,
              onlyCategories: ["performance"],
              screenEmulation: {
                ...viewport,
                deviceScaleFactor: desktop ? 1 : 1.75,
                mobile: !desktop,
                disabled: false,
              },
              logLevel: "error",
            },
            desktop ? desktopConfig : undefined,
          );
          assert.equal(result.lhr.runtimeError, undefined);
          await writeFile(
            `${output}/${profile}-${path.slice(1)}-${run}.json`,
            JSON.stringify(result.lhr),
          );
          await writeFile(`${output}/${profile}-${path.slice(1)}-${run}.html`, result.report);
          console.log(`Route styles ${phase}: ${profile} ${path} run ${run + 1}`);
        } finally {
          await chrome.kill();
        }
      }
    }
  }
  await writeFile(`${output}/coverage.json`, JSON.stringify(measurements, null, 2));
  const reports = await Promise.all(
    (await readdir(output))
      .filter((name) => /^(mobile|desktop)-.*-\d\.json$/.test(name))
      .map(async (name) => ({
        name,
        lhr: JSON.parse(await readFile(`${output}/${name}`, "utf8")),
      })),
  );
  const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const summary = [];
  for (const profile of ["mobile", "desktop"]) {
    for (const path of ["/latest", "/topics", "/leaderboard"]) {
      const rows = reports.filter(({ name }) => name.startsWith(`${profile}-${path.slice(1)}-`));
      assert.equal(rows.length, 3);
      const sheets = measurements.find(
        (row) => row.profile === profile && row.path === path,
      ).sheets;
      const stylesheets = rows.map(({ lhr }) =>
        lhr.audits["resource-summary"].details.items.find(
          (item) => item.resourceType === "stylesheet",
        ),
      );
      summary.push({
        profile,
        path,
        cssBytes: sheets.reduce((sum, sheet) => sum + sheet.bytes, 0),
        unusedCssBytes: sheets.reduce((sum, sheet) => sum + sheet.bytes - sheet.usedBytes, 0),
        cssTransferBytes: median(stylesheets.map((item) => item.transferSize)),
        cssRequests: median(stylesheets.map((item) => item.requestCount)),
        renderBlockingCssRequests: median(
          rows.map(({ lhr }) => lhr.audits["render-blocking-resources"].details.items.length),
        ),
        renderBlockingCssBytes: median(
          rows.map(({ lhr }) =>
            lhr.audits["render-blocking-resources"].details.items.reduce(
              (sum, item) => sum + item.totalBytes,
              0,
            ),
          ),
        ),
        lighthouseUnusedCssBytes: median(
          rows.map(({ lhr }) => lhr.audits["unused-css-rules"].details.overallSavingsBytes ?? 0),
        ),
        renderBlockingMs: median(
          rows.map(({ lhr }) => lhr.audits["render-blocking-resources"].numericValue),
        ),
        fcpMs: median(rows.map(({ lhr }) => lhr.audits["first-contentful-paint"].numericValue)),
        cls: median(rows.map(({ lhr }) => lhr.audits["cumulative-layout-shift"].numericValue)),
      });
    }
  }
  await writeFile(`${output}/summary.json`, JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
}
