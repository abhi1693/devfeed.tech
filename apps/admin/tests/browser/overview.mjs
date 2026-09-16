import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";

// Run after npm run admin:build. All API traffic stays on disposable localhost servers.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const bundled = await build({
  entryPoints: [`${root}/apps/admin/tests/fixtures/overview.ts`],
  bundle: true,
  write: false,
  format: "esm",
});
const { populatedOverview, emptyOverview } = await import(
  `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`
);
let mode = "populated",
  active = 0,
  maximum = 0;
const requests = [];
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  let body = {};
  if (url.pathname.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      name: "Overview reviewer",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (url.pathname.endsWith("/settings"))
    body = {
      appearance: { theme: "dark" },
      defaults: { refresh_seconds: mode.startsWith("live") ? 5 : 0, overview_days: 30 },
    };
  else if (url.pathname.endsWith("/notifications/config")) body = { enabled: false };
  else if (url.pathname.endsWith("/ai/connection"))
    body = {
      state: "connected",
      message: "Connected",
      email: "reviewer@example.com",
      model: "legacy-model",
      quota: [{ used_percent: 5, window_minutes: 10080, resets_at: "2026-09-20T21:19:48Z" }],
    };
  else if (url.pathname.includes("/overview/panels/")) {
    const panel = url.pathname.split("/").at(-1);
    requests.push({ panel, days: url.searchParams.get("days") });
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, mode === "loading" ? 500 : 20));
    active--;
    body = structuredClone(mode === "empty" ? emptyOverview : populatedOverview);
    body.generated_at = new Date(Date.now() - (mode === "stale" ? 600000 : 0)).toISOString();
    body.days = Number(url.searchParams.get("days"));
    if (mode !== "empty") {
      body.activity = Array.from({ length: 30 }, (_, i) => ({
        date: new Date(Date.UTC(2026, 8, i + 1)).toISOString().slice(0, 10),
        added: i > 26 ? 42 : 0,
        published: i > 26 ? 27 : 0,
      }));
      body.insights.reader_activity = body.activity.map((row, index) => ({
        ...row,
        opens: index < 5 ? null : index > 26 ? 120 : 0,
        readers: index < 5 ? null : index > 26 ? 40 : 0,
        accounts: index > 26 ? 4 : 0,
        multi_article_readers: index < 5 ? null : index > 26 ? 15 : 0,
        content_types: {},
      }));
      body.automation.throughput = {
        capacity_observed: true,
        topic_workers: 2,
        article_workers: 2,
        eligible_topic_workers: 4,
        eligible_article_workers: 4,
        busy_topic_workers: 2,
        busy_article_workers: 2,
        idle_topic_workers: 2,
        idle_article_workers: 2,
        shared_workers: 4,
        topic_decisions_per_hour: 4,
        articles_published_per_hour: 28,
      };
      body.automation.topic_decisions = {
        pending: 1765,
        actionable: 4,
        deferred: 1750,
        awaiting_review: 11,
        decisions_per_hour: 4,
        estimated_drain_hours: 1,
      };
      body.automation.throughput.hours = [
        {
          hour: "2026-09-30T12:00:00Z",
          topic_decisions: 4,
          articles_published: 28,
          article_analyses: 30,
        },
      ];
      body.automation.inference = {
        first_recorded_at: "2026-09-28T00:00:00Z",
        activity: [
          {
            date: "2026-09-30",
            operation: "article_analysis",
            model: "example-model",
            effort: "low",
            calls: 2,
            returned: 1,
            failed: 1,
            running: 0,
            unreported: 1,
            input_tokens: 1000,
            cached_input_tokens: 200,
            output_tokens: 300,
            reasoning_tokens: 100,
            web_searches: 2,
            duration_ms: 1200,
          },
        ],
      };
      body.insights.coverage.push({
        ...body.insights.coverage[0],
        id: "topic-2",
        name: "TypeScript",
        publications: 20,
      });
      body.insights.processing[0].queued = mode === "live-changed" ? 8987 : 9011;
      body.insights.processing[0].running = mode === "live-changed" ? 3 : 2;
      if (mode.startsWith("live"))
        body.insights.processing.push(
          ...[
            { kind: "ingestion", queued: 4, running: 1 },
            { kind: "images", queued: 7, running: 2 },
            { kind: "topic-analysis", queued: 20, running: 1 },
            { kind: "notifications", queued: 0, running: 2 },
          ].map((row) => ({ ...row, completed: 0, failed: 0, oldest_queued_at: null })),
        );
    }
    if (
      (mode === "failure" && panel === "publications") ||
      (mode === "diagnostic-failure" && panel === "tokens-by-model") ||
      (mode === "live-failure" && panel === "workload")
    ) {
      res.statusCode = 500;
      body = { detail: "Fixture panel failure" };
    }
  }
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const portProbe = createServer();
await new Promise((resolve) => portProbe.listen(0, "127.0.0.1", resolve));
const port = portProbe.address().port;
await new Promise((resolve) => portProbe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
);
const app = spawn(
  process.execPath,
  [
    `${root}/node_modules/next/dist/bin/next`,
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: `${root}/apps/admin`,
    env: {
      ...env,
      DEVFEED_ADMIN_API_URL: `http://127.0.0.1:${fixture.address().port}`,
      DEVFEED_ADMIN_BASE_URL: origin,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (chunk) => {
  logs += chunk;
  console.log(String(chunk));
});
app.stderr.on("data", (chunk) => (logs += chunk));
let browser;
const output = `${root}/reports/admin-overview`;
await mkdir(output, { recursive: true });
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      await fetch(`${origin}/login`, { signal: AbortSignal.timeout(1000) });
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const settled = () =>
    page.waitForFunction(
      () => {
        const panels = document.querySelectorAll("[data-overview-panel]");
        return (
          panels.length === 33 &&
          [...panels].every((panel) => panel.getAttribute("aria-busy") === "false")
        );
      },
      undefined,
      { timeout: 30000 },
    );
  console.log("Opening", origin);
  await page.goto(origin);
  assert.equal(await page.getByRole("link", { name: "Knowledge graph" }).count(), 0);
  const removed = await context.newPage();
  await removed.goto(`${origin}/knowledge/graph`);
  await removed.getByRole("heading", { name: "404" }).waitFor();
  await removed.close();
  console.log("Loaded", page.url());
  await settled();
  assert.equal(await page.locator("main h2").count(), 6);
  assert.equal(await page.getByRole("navigation", { name: "Overview sections" }).count(), 1);
  assert.ok(maximum <= 4, `Concurrent overview requests: ${maximum}`);
  await page.getByRole("button", { name: "AI connection: AI connected" }).click();
  const connection = page.getByRole("dialog", { name: "AI connection" });
  await connection.getByRole("progressbar", { name: "Weekly quota" }).waitFor();
  assert.equal(await connection.getByText("5% used").count(), 1);
  assert.equal(await connection.getByText("Model", { exact: true }).count(), 0);
  await connection.screenshot({ path: `${output}/ai-quota.png` });
  await page.keyboard.press("Escape");
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await page.locator("#overview-panel-workload").screenshot({ path: `${output}/workload.png` });
  await page.locator("#processing").screenshot({ path: `${output}/processing.png` });
  assert.match(
    await page.getByRole("table", { name: "Worker capacity" }).innerText(),
    /Topic analysis\s+2\s+2\s+4/,
  );
  // The header scrolls away; the sticky sidebar must still fill the viewport.
  for (const height of [1080, 600]) {
    await page.setViewportSize({ width: 1920, height });
    await page.evaluate(() => window.scrollTo(0, 1200));
    const sidebar = page.locator("#admin-sidebar");
    const bounds = await sidebar.boundingBox();
    assert.ok(Math.abs(bounds.y) <= 1, "Sidebar should stick to the viewport top");
    assert.ok(
      Math.abs(bounds.y + bounds.height - height) <= 1,
      "Sidebar should reach the viewport bottom",
    );
    await sidebar.evaluate((node) => (node.scrollTop = node.scrollHeight));
    const last = await sidebar.getByRole("link").last().boundingBox();
    assert.ok(last.y >= 0 && last.y + last.height <= height, "Last navigation item is reachable");
    await page.screenshot({ path: `${output}/sidebar-scrolled-${height}.png` });
    await sidebar.evaluate((node) => (node.scrollTop = 0));
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const readerCard = page
    .getByRole("heading", { name: "Readers opening articles", exact: true })
    .locator("xpath=ancestor::section[1]");
  const adoptionCard = page
    .getByRole("heading", { name: "Likes and follows", exact: true })
    .locator("xpath=ancestor::section[1]");
  const readerBounds = await readerCard.boundingBox();
  const adoptionBounds = await adoptionCard.boundingBox();
  assert.ok(
    Math.abs(readerBounds.y - adoptionBounds.y) <= 1,
    "Sparse-history controls must not offset adjacent cards",
  );
  const periodButton = readerCard.getByRole("button", { name: "Show full period" });
  const buttonBounds = await periodButton.boundingBox();
  assert.ok(
    buttonBounds.y >= readerBounds.y && buttonBounds.y < readerBounds.y + 60,
    "Period control belongs inside the card header",
  );
  const toggle = page.getByRole("button", { name: "Show full period" }).first();
  await toggle.click();
  await page.getByRole("button", { name: "Focus on activity" }).first().click();
  assert.equal(await page.locator("main details").count(), 0);
  const panels = await page.locator("[data-overview-panel]").all();
  assert.equal(panels.length, 33);
  for (const panel of panels)
    assert.ok(await panel.isVisible(), "Every panel is visible without expanding anything");
  await page.setViewportSize({ width: 390, height: 844 });
  // Responsive charts resize through ResizeObserver after the viewport changes.
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth, undefined, {
    timeout: 5000,
  });
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    `Mobile horizontal overflow: ${JSON.stringify(
      await page.evaluate(() =>
        [...document.querySelectorAll("main *")]
          .filter((node) => node.getBoundingClientRect().right > innerWidth)
          .slice(0, 20)
          .map((node) => ({
            tag: node.tagName,
            cls: node.className,
            width: node.getBoundingClientRect().width,
            text: node.textContent.slice(0, 80),
          })),
      ),
    )}`,
  );
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  await page.evaluate(() => {
    window.scrollTo(0, 0);
  });
  await page.screenshot({ path: `${output}/mobile-top.png` });
  assert.equal(await page.getByRole("button", { name: "Refresh all" }).count(), 0);
  assert.equal(await page.getByRole("status", { name: "Overview refresh status" }).count(), 0);
  mode = "diagnostic-failure";
  await page.getByRole("button", { name: "7 days", exact: true }).click();
  await page.locator("#overview-panel-tokens-by-model").getByRole("alert").waitFor();
  mode = "failure";
  await page.getByRole("button", { name: "30 days", exact: true }).click();
  await page.locator("#overview-panel-publications").getByRole("alert").waitFor();
  await page.screenshot({ path: `${output}/partial-failure.png`, fullPage: true });
  mode = "loading";
  await page.getByRole("button", { name: "7 days", exact: true }).click();
  await page.locator('[data-overview-panel][aria-busy="true"]').first().waitFor();
  await page.screenshot({ path: `${output}/loading.png` });
  await settled();
  mode = "empty";
  await page.reload();
  await settled();
  await page.screenshot({ path: `${output}/empty.png`, fullPage: true });
  mode = "stale";
  await page.reload();
  await page
    .getByText(/Stale data/)
    .first()
    .waitFor();
  mode = "populated";
  await page.getByRole("button", { name: "7 days", exact: true }).click();
  await settled();
  assert.equal(requests.at(-1).days, "7");
  mode = "live";
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.reload();
  await settled();
  const workload = page.locator("#overview-panel-workload");
  await workload
    .getByRole("status", { name: "Workload live status" })
    .filter({ hasText: "Live" })
    .waitFor();
  assert.equal(await workload.getByText("Initial observation", { exact: true }).count(), 2);
  mode = "live-changed";
  await workload.getByText("−24 since last check", { exact: true }).waitFor({ timeout: 15000 });
  await workload.getByText("+1 since last check", { exact: true }).waitFor();
  await workload.screenshot({ path: `${output}/workload-live.png` });
  assert.equal(
    await workload
      .getByRole("table", { name: "Workload counts by job type" })
      .getByRole("link")
      .count(),
    8,
  );
  assert.equal(
    await workload
      .getByRole("figure", { name: "Running jobs by type" })
      .locator("circle[stroke-dasharray]")
      .count(),
    5,
  );
  assert.ok((await workload.boundingBox()).height < 450, "Workload should remain a compact chart");
  assert.equal(await workload.getByRole("button", { name: /Pause|Resume/ }).count(), 0);
  mode = "live-failure";
  await workload.getByText("Reconnecting", { exact: true }).waitFor({ timeout: 15000 });
  assert.ok(await workload.locator("dd").filter({ hasText: "9,018" }).count());
  mode = "live-changed";
  await workload
    .getByRole("status", { name: "Workload live status" })
    .filter({ hasText: "Live" })
    .waitFor({ timeout: 15000 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  await workload.screenshot({ path: `${output}/workload-live-mobile.png` });
  assert.deepEqual(errors, []);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        maximumConcurrentPanelRequests: maximum,
        scenarios: [
          "desktop",
          "mobile",
          "all 33 panels visible without expansion",
          "sparse/full history",
          "partial failure retains data",
          "loading",
          "empty",
          "stale",
          "range change",
          "keyboard navigation to diagnostic failure",
          "sticky sidebar at 1080px and 600px heights",
          "live polling, deltas, reconnect and recovery",
        ],
        pageErrors: errors,
      },
      null,
      2,
    ),
  );
  console.log(`Playwright overview scenarios passed. Screenshots: ${output}`);
} catch (error) {
  console.error(logs.slice(-4000));
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  fixture.close();
}
