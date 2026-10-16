// Build first; reports, coverage and screenshots remain in ignored reports/.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { build } from "esbuild";
import { chromium } from "playwright";
import { leaderboardProfile } from "../../apps/web/tests/browser/leaderboard.mjs";
function usedBytes(entry) {
  const ranges = entry.functions.flatMap((f) => f.ranges);
  const events = ranges
    .flatMap((r) => [
      { at: r.startOffset, start: true, r },
      { at: r.endOffset, start: false, r },
    ])
    .sort((a, b) => a.at - b.at);
  const active = new Set();
  let previous = 0,
    used = 0;
  for (const event of events) {
    const inner = [...active].sort(
      (a, b) => a.endOffset - a.startOffset - (b.endOffset - b.startOffset),
    )[0];
    if (inner?.count > 0) used += Buffer.byteLength(entry.source.slice(previous, event.at));
    if (event.start) active.add(event.r);
    else active.delete(event.r);
    previous = event.at;
  }
  return used;
}
// Fix Faro's sampling decision for a comparable 90%-of-sessions path; UUID entropy is untouched.
function unsampledSession(value = 0xffffffff) {
  const random = crypto.getRandomValues.bind(crypto);
  crypto.getRandomValues = (array) => {
    if (array instanceof Uint32Array && array.length === 1) {
      array[0] = value;
      return array;
    }
    return random(array);
  };
}
const phase = process.argv[2] || "before";
const root = `reports/reader-startup/${phase}`;
await mkdir(root, { recursive: true });
const bundle = await build({
  entryPoints: ["apps/web/tests/fixtures.ts"],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const articles = Array.from({ length: 24 }, (_, i) => ({
  ...article,
  id: `22222222-2222-4222-8222-${String(i).padStart(12, "0")}`,
  slug: `article-${i}`,
  title: `${article.title} ${i}`,
}));
const fixture = createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const p = url.pathname;
  let body = [];
  if (p === "/v1/user/auth/me") body = null;
  else if (p === "/v1/user/auth/config") body = { enabled: true, providers: [] };
  else if (p.includes("reading-heatmap")) body = null;
  else if (p.startsWith("/v1/user/profiles/"))
    body = {
      ...leaderboardProfile,
      links: [{ url: "https://github.com/example", label: "GitHub" }],
      avatar_url: null,
    };
  else if (p === "/v1/feed") body = { items: articles, next_cursor: null };
  else if (p === "/v1/feed/options")
    body = { sources: [source], content_types: ["tutorial"], languages: ["en"] };
  else if (p === "/v1/topics") body = [topic];
  else if (p === "/v1/sources") body = [source];
  else if (p.startsWith("/v1/articles/")) body = article;
  res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const api = `http://127.0.0.1:${fixture.address().port}`;
const port = 18729;
const origin = `http://127.0.0.1:${port}`;
const app = spawn(
  process.execPath,
  [
    "node_modules/next/dist/bin/next",
    "start",
    "apps/web",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    env: {
      ...process.env,
      DEVFEED_PUBLIC_API_URL: api,
      DEVFEED_USER_API_URL: api,
      DEVFEED_USER_BASE_URL: origin,
      DEVFEED_FARO_ENABLED: "true",
      DEVFEED_TELEMETRY_ENVIRONMENT: "benchmark",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (d) => (logs += d));
app.stderr.on("data", (d) => (logs += d));
let browser;
try {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`${origin}/api/v1/topics`)).ok) break;
    } catch {}
    if (i === 99) throw new Error(logs);
    await new Promise((r) => setTimeout(r, 100));
  }
  browser = await chromium.launch({ headless: true });
  const results = [];
  for (const route of ["/latest", "/topics", "/sources", "/users/leader-reader"]) {
    const context = await browser.newContext({ viewport: { width: 1350, height: 940 } });
    await context.addInitScript(unsampledSession);
    const page = await context.newPage();
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(origin + route);
    await page.locator("h1").first().waitFor();
    await page.waitForTimeout(2000);
    const coverage = await page.coverage.stopJSCoverage();
    const chunks = coverage
      .filter((c) => c.url.startsWith(origin))
      .map((c) => ({
        url: c.url,
        totalBytes: Buffer.byteLength(c.source),
        usedBytes: usedBytes(c),
        text: c.source,
      }));
    await writeFile(`${root}/${route.replaceAll("/", "_")}-coverage.json`, JSON.stringify(chunks));
    const tracingDownloaded = chunks.some(
      (chunk) => chunk.totalBytes > 50_000 && chunk.text.includes("TracingInstrumentation"),
    );
    if (phase.startsWith("after"))
      assert.equal(tracingDownloaded, false, "unsampled sessions do not download tracing");
    const totals = chunks.reduce(
      (a, c) => ({
        totalBytes: a.totalBytes + c.totalBytes,
        unusedBytes: a.unusedBytes + c.totalBytes - c.usedBytes,
      }),
      { totalBytes: 0, unusedBytes: 0 },
    );
    if (phase.startsWith("after") && route.startsWith("/users/"))
      assert.equal(
        await page.locator("script[data-devfeed-ga]").count(),
        0,
        "passive views do not fetch Google Analytics",
      );
    const brand = page.locator(".profile-brand-mark");
    if (await brand.count()) {
      const mask = await brand.first().evaluate((node) => getComputedStyle(node).maskImage);
      const pathname = mask.match(/url\(["']?(.*?)["']?\)/)?.[1];
      const asset = await fetch(pathname);
      assert.ok(asset.ok);
      assert.match(asset.headers.get("content-type"), /image\/svg\+xml/);
      assert.equal(asset.headers.get("cache-control"), "public, max-age=31536000, immutable");
    }
    const interactionStart = performance.now();
    await page.keyboard.press("/");
    await page.locator("#search").evaluate((node) => {
      if (document.activeElement !== node) throw new Error("Search shortcut did not focus");
    });
    const searchFocusMs = performance.now() - interactionStart;
    assert.equal(
      await page.locator("script[data-devfeed-ga]").count(),
      1,
      "first interaction initializes configured analytics",
    );
    results.push({ route, ...totals, tracingDownloaded, searchFocusMs, errors });
    assert.deepEqual(errors, []);
    await page.screenshot({ path: `${root}/${route.replaceAll("/", "_")}.png`, fullPage: true });
    await context.close();
  }
  // Exercise real compiled SDK initialization for both sampled and DNT sessions.
  for (const sampled of [true, false]) {
    const context = await browser.newContext();
    if (sampled) await context.addInitScript(unsampledSession, 0);
    else
      await context.addInitScript(() =>
        Object.defineProperty(navigator, "doNotTrack", { get: () => "1" }),
      );
    const page = await context.newPage();
    await page.coverage.startJSCoverage();
    await page.goto(origin + "/latest");
    await page.waitForTimeout(2000);
    const coverage = await page.coverage.stopJSCoverage();
    const tracing = coverage.some(
      (entry) =>
        (entry.source?.length ?? 0) > 50_000 && entry.source.includes("TracingInstrumentation"),
    );
    assert.equal(
      tracing,
      sampled,
      sampled ? "sampled sessions load tracing" : "DNT sessions skip tracing",
    );
    if (!sampled)
      assert.equal(
        coverage.some(
          (entry) =>
            (entry.source?.length ?? 0) > 50_000 && entry.source.includes("initializeFaro"),
        ),
        false,
        "DNT sessions skip the Faro SDK",
      );
    const request = page.waitForRequest((request) => request.url() === origin + "/api/v1/topics");
    await page.evaluate(() => fetch("/api/v1/topics"));
    const traceparent = (await request).headers()["traceparent"];
    if (sampled) assert.match(traceparent, /^00-[a-f0-9]{32}-[a-f0-9]{16}-01$/);
    else assert.equal(traceparent, undefined);
    await context.close();
  }
  if (process.env.DEVFEED_STARTUP_LIGHTHOUSE === "1") {
    const { default: lighthouse } = await import("lighthouse");
    const { launch } = await import("chrome-launcher");
    const { default: desktop } = await import("lighthouse/core/config/desktop-config.js");
    for (const profile of ["mobile", "desktop"])
      for (const route of process.env.DEVFEED_STARTUP_ROUTES?.split(",") ?? [
        "/latest",
        "/topics",
        "/users/leader-reader",
      ])
        for (let repeat = 1; repeat <= 3; repeat++) {
          const chrome = await launch({
            chromePath: chromium.executablePath(),
            chromeFlags: ["--headless", "--no-sandbox"],
          });
          try {
            const connection = await chromium.connectOverCDP(`http://127.0.0.1:${chrome.port}`);
            await connection.contexts()[0].addInitScript(unsampledSession);
            const result = await lighthouse(
              origin + route,
              {
                port: chrome.port,
                output: ["json", "html"],
                onlyCategories: ["performance"],
                logLevel: "error",
              },
              profile === "desktop" ? desktop : undefined,
            );
            const name = `${root}/${profile}-${route.replaceAll("/", "_")}-${repeat}`;
            await writeFile(`${name}.json`, result.report[0]);
            await writeFile(`${name}.html`, result.report[1]);
            await connection.close();
          } finally {
            await chrome.kill();
          }
        }
  }
  await writeFile(`${root}/summary.json`, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.close();
}
