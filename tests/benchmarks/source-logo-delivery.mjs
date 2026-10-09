// Compare identical saved publisher artwork before/after ingestion, including warm cache.
// First run source_logo_encoding.py; reports and screenshots stay ignored/local.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve("reports/source-logos");
const assets = JSON.parse(await readFile(path.join(root, "encoding.json"), "utf8"));
let hits = 0;
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
      const managed = url.searchParams.get("phase") === "after";
      const images = assets
        .flatMap((asset) =>
          [12, 23, 30].map((size) => {
            const src = managed ? asset.variants.find((v) => v.width === 64).key : asset.input;
            const srcset = managed
              ? asset.variants.map((v) => `/${v.key} ${v.width}w`).join(",")
              : "";
            return `<img alt="" width="${size}" height="${size}" src="/${src}" srcset="${srcset}" sizes="${size}px">`;
          }),
        )
        .join("");
      res.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "no-store" });
      res.end(
        `<!doctype html><html lang="en"><title>Publisher logo delivery</title><meta name="viewport" content="width=device-width"><body><h1>Publisher logos</h1>${images}</body></html>`,
      );
    } else {
      hits++;
      const managed = url.pathname.endsWith(".webp");
      const body = await readFile(path.join(root, url.pathname));
      res.writeHead(200, {
        "Content-Type": managed ? "image/webp" : "image/png",
        "Cache-Control": managed
          ? "public, max-age=31536000, immutable"
          : assets.find((asset) => `/${asset.input}` === url.pathname)?.original_cache_control ||
            "no-store",
      });
      res.end(body);
    }
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const [name, width, height, dpr] of [
    ["mobile", 412, 823, 1.75],
    ["desktop", 1350, 940, 1],
    ["retina", 1350, 940, 2],
  ]) {
    for (const phase of ["before", "after"]) {
      const context = await browser.newContext({
        viewport: { width, height },
        deviceScaleFactor: dpr,
      });
      const page = await context.newPage();
      hits = 0;
      await page.goto(`${origin}/?phase=${phase}`);
      const images = await page.locator("img").evaluateAll((images) =>
        images.map((image) => ({
          currentSrc: image.currentSrc,
          renderedWidth: image.width,
          naturalWidth: image.naturalWidth,
          complete: image.complete,
        })),
      );
      assert.ok(images.every((image) => image.complete && image.naturalWidth > 0));
      const cold = await page.evaluate(() =>
        performance
          .getEntriesByType("resource")
          .filter((r) => r.initiatorType === "img")
          .reduce((sum, r) => sum + r.transferSize, 0),
      );
      const coldHits = hits;
      await page.screenshot({ path: path.join(root, `${name}-${phase}.png`) });
      // A new document in the same profile must reuse immutable managed responses.
      await page.goto(`${origin}/?phase=${phase}&warm=1`);
      const warm = await page.evaluate(() =>
        performance
          .getEntriesByType("resource")
          .filter((r) => r.initiatorType === "img")
          .reduce((sum, r) => sum + r.transferSize, 0),
      );
      if (phase === "after") {
        assert.equal(hits, coldHits);
        assert.equal(warm, 0);
      }
      results.push({
        name,
        phase,
        dpr,
        coldTransferBytes: cold,
        warmTransferBytes: warm,
        coldRequests: coldHits,
        warmRequests: hits - coldHits,
        images,
      });
      await context.close();
    }
  }
  if (process.env.DEVFEED_LOGO_LIGHTHOUSE === "1") {
    const { default: lighthouse } = await import("lighthouse");
    const { launch } = await import("chrome-launcher");
    const { default: desktopConfig } = await import("lighthouse/core/config/desktop-config.js");
    for (const profile of ["mobile", "desktop"]) {
      for (const phase of ["before", "after"]) {
        const chrome = await launch({
          chromePath: chromium.executablePath(),
          chromeFlags: ["--headless", "--no-sandbox"],
        });
        try {
          const report = await lighthouse(
            `${origin}/?phase=${phase}`,
            {
              port: chrome.port,
              output: ["json", "html"],
              onlyCategories: ["performance"],
              logLevel: "error",
            },
            profile === "desktop" ? desktopConfig : undefined,
          );
          await writeFile(path.join(root, `${profile}-${phase}-lighthouse.json`), report.report[0]);
          await writeFile(path.join(root, `${profile}-${phase}-lighthouse.html`), report.report[1]);
        } finally {
          await chrome.kill();
        }
      }
    }
  }
  await writeFile(path.join(root, "delivery.json"), JSON.stringify(results, null, 2) + "\n");
  console.log(
    JSON.stringify(
      results.map(({ images, ...result }) => result),
      null,
      2,
    ),
  );
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
