import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. The API is a disposable local fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
let pollingMode = "adaptive";
const patches = [];
const fixture = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = {};
  if (req.method === "PATCH") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const patch = JSON.parse(raw);
    patches.push(patch);
    pollingMode = patch.polling_mode;
  }
  if (path.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: 4102444800,
      csrf_token: "fixture",
    };
  else if (path.endsWith("/settings"))
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0 } };
  else if (path.endsWith("/notifications/config")) body = { enabled: false };
  else if (path.endsWith("/sources/source-1"))
    body = {
      id: "source-1",
      name: "Engineering Leadership",
      slug: "engineering-leadership",
      feed_url: "https://newsletter.eng-leadership.com/feed",
      source_type: "publisher",
      enabled: true,
      poll_interval_seconds: 43200,
      polling_mode: pollingMode,
      adaptive_polling_enabled: false,
      effective_polling_mode: "fixed",
      effective_interval_seconds: 43200,
      polling_state: { reason: "quiet", interval: 86400, observed_at: "2026-10-10T00:00:00Z" },
      approval_status: "approved",
      publication_policy: "manual",
      publication_policy_revision: 0,
    };
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify(body));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
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
app.stdout.on("data", (chunk) => (logs += chunk));
app.stderr.on("data", (chunk) => (logs += chunk));
let browser;
try {
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(`${origin}/login`);
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/content/sources/source-1`);
  await page.getByRole("heading", { name: "Engineering Leadership", exact: true }).waitFor();
  await page.getByText("Effective polling mode", { exact: true }).waitFor();
  await page.getByText("Effective interval seconds", { exact: true }).waitFor();
  await page.getByText("Adaptive polling enabled", { exact: true }).waitFor();
  await mkdir(`${root}/reports/adaptive-polling`, { recursive: true });
  await page.screenshot({ path: `${root}/reports/adaptive-polling/detail.png`, fullPage: true });
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL("**/source-1/edit");
  const mode = page.getByRole("combobox", { name: "Polling mode", exact: false });
  await mode.waitFor();
  assert.match(await mode.innerText(), /adaptive/i);
  await mode.click();
  await page.getByRole("option", { name: /^fixed$/i }).click();
  assert.match(await mode.innerText(), /fixed/i);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL("**/sources/source-1");
  assert.equal(patches.length, 1);
  assert.equal(patches[0].polling_mode, "fixed");
  assert.equal(patches[0].poll_interval_seconds, 43200);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL("**/source-1/edit");
  await mode.click();
  await page.getByRole("option", { name: /^adaptive$/i }).click();
  assert.equal(
    await page.getByLabel("Poll interval (seconds)", { exact: false }).inputValue(),
    "43200",
  );
  await page.screenshot({ path: `${root}/reports/adaptive-polling/controls.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: `${root}/reports/adaptive-polling/mobile.png`, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(
    "Adaptive polling admin browser checks passed: effective gate state, separate configured interval, mode controls, mobile layout.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}
