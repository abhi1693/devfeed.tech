import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";

// Run after npm run admin:build. Authentication and API traffic use disposable local fixtures.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const bundled = await build({
  entryPoints: [`${root}/apps/admin/tests/fixtures/push-analytics.ts`],
  bundle: true,
  write: false,
  format: "esm",
});
const { pushAnalyticsFixture, emptyPushAnalyticsFixture } = await import(
  `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`
);
let mode = "populated";
const requests = [];
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  let body = {};
  if (url.pathname.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      name: "Push analytics reviewer",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (url.pathname.endsWith("/settings"))
    body = {
      appearance: { theme: "dark" },
      defaults: { refresh_seconds: 0, overview_days: 30 },
    };
  else if (url.pathname.endsWith("/notifications/config")) body = { enabled: false };
  else if (url.pathname.endsWith("/ai/connection"))
    body = { state: "disabled", message: "Disabled" };
  else if (url.pathname.endsWith("/push-analytics")) {
    const days = Number(url.searchParams.get("days"));
    requests.push({ days, mode });
    body = mode === "empty" ? emptyPushAnalyticsFixture(days) : pushAnalyticsFixture(days);
    if (mode === "loading") await new Promise((resolve) => setTimeout(resolve, 1000));
    if (mode === "disabled") body.enabled = false;
    if (mode === "missing-receipts") {
      body.totals = { ...body.totals, displayed: 0, clicked: 3, opened: 2, click_rate: 0 };
      body.by_kind = [{ kind: "daily_must_read", ...body.totals }];
    }
    if (mode === "stale") body.generated_at = new Date(Date.now() - 600000).toISOString();
    if (mode === "failure") {
      res.statusCode = 500;
      body = { detail: "Private fixture error details must stay hidden" };
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
});
app.stderr.on("data", (chunk) => {
  logs += chunk;
});
const output = "/tmp/devfeed-push-analytics-review";
await mkdir(output, { recursive: true });
let browser;
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
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  await context.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) =>
      window.__cspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`),
    );
  });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.clock.install();
  const settled = () =>
    page.waitForFunction(
      () =>
        document.querySelector('main [aria-busy="false"]') &&
        !document.querySelector('main [aria-busy="true"]'),
    );
  await page.goto(`${origin}/push-analytics`);
  await page.bringToFront();
  await page.getByRole("heading", { name: "Push analytics", exact: true }).waitFor();
  await page.getByRole("table", { name: "Notification outcomes by type" }).waitFor();
  await settled();
  assert.equal(requests[0].days, 30);
  assert.equal(
    await page
      .getByRole("link", { name: "Push analytics", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  const table = page.getByRole("table", { name: "Notification outcomes by type" });
  assert.match(await table.innerText(), /Daily Must Read\s+7\s+28\s+21\s+14\s+7\s+5\s+50.0%/);
  assert.ok(
    await page
      .getByRole("figure", { name: "Notification outcomes by UTC publication date" })
      .locator("svg")
      .count(),
  );
  assert.ok(await page.getByText(/Later outcomes update that same cohort/).count());
  assert.ok(await page.getByText(/An open means navigation succeeded/).count());
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });

  for (const width of [768, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.screenshot({ path: `${output}/width-${width}.png`, fullPage: true });
  }
  await page.getByRole("button", { name: "Navigation", exact: true }).click();
  await page.getByRole("link", { name: "Push analytics", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "Navigation", exact: true })
      .getAttribute("aria-expanded"),
    "false",
  );

  mode = "missing-receipts";
  await page.getByRole("button", { name: "7 days", exact: true }).click();
  await page.getByText("No reported displays to calculate a click rate.").waitFor();
  assert.equal(requests.at(-1).days, 7);
  assert.equal(await page.getByText("—", { exact: true }).count(), 2);
  await page.screenshot({ path: `${output}/missing-receipts.png`, fullPage: true });

  mode = "failure";
  await page.clock.fastForward(60_000);
  await page
    .getByRole("alert")
    .filter({ hasText: "Showing the last successful result." })
    .waitFor();
  assert.ok(await table.count());
  assert.equal(await page.getByText(/Private fixture error/).count(), 0);
  assert.match(
    await page.getByRole("status", { name: "Push analytics refresh status" }).innerText(),
    /Stale data/,
  );
  await page.screenshot({ path: `${output}/poll-failure.png`, fullPage: true });
  mode = "populated";
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Push analytics could not be updated." })
    .waitFor({ state: "hidden" });
  await settled();

  mode = "loading";
  await page.getByRole("button", { name: "90 days", exact: true }).click();
  await page.getByText("Loading push analytics…").waitFor();
  await page.screenshot({ path: `${output}/loading.png` });
  await table.waitFor();
  assert.equal(requests.at(-1).days, 90);

  mode = "empty";
  await page.reload();
  await page.getByText("No notification events published in this period.").waitFor();
  await page.getByText("No browser deliveries in this period.").waitFor();
  await page.screenshot({ path: `${output}/empty.png`, fullPage: true });
  mode = "disabled";
  await page.reload();
  await page.getByText("Browser push is disabled.").waitFor();
  await table.waitFor();
  mode = "stale";
  await page.reload();
  await page
    .getByRole("status", { name: "Push analytics refresh status" })
    .filter({ hasText: "Stale data" })
    .waitFor();
  mode = "failure";
  await page.reload();
  await page
    .getByRole("alert")
    .filter({ hasText: "Push analytics could not be updated." })
    .waitFor();
  assert.equal(await table.count(), 0);
  mode = "populated";
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await table.waitFor();
  assert.deepEqual(errors, []);
  assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        requests,
        scenarios: [
          "desktop",
          "768/390/320px without page overflow",
          "mobile sidebar navigation",
          "UTC cohort chart",
          "7/30/90-day windows",
          "missing browser receipts",
          "polling failure preserves data",
          "retry recovery",
          "loading",
          "empty",
          "disabled with historical outcomes",
          "stale generated data",
          "initial error",
        ],
        pageErrors: errors,
      },
      null,
      2,
    ),
  );
  console.log(`Push analytics browser scenarios passed. Local screenshots: ${output}`);
} catch (error) {
  console.error(logs.slice(-4000));
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  fixture.close();
}
