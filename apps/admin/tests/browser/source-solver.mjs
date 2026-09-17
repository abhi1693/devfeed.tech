import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. The API is a disposable local fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const requests = [];
const feed = "https://publication.example/feed";
const fixture = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = {};
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
  else if (path.endsWith("/sources/preview")) {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const input = JSON.parse(raw);
    requests.push(input);
    if (!input.use_solver) {
      res.statusCode = 422;
      body = {
        detail: { code: "browser_challenge", message: "Publisher requires browser verification." },
      };
    } else
      body = {
        name: "Engineering feed",
        description: "Engineering articles",
        website_url: null,
        logo_url: null,
        image_url: null,
        language: "en",
        entries_seen: 10,
        entries_skipped: 0,
        warnings: [],
      };
  }
  if (path.endsWith("/sources/source-1"))
    body = {
      id: "source-1",
      name: "Existing source",
      feed_url: feed,
      source_type: "publisher",
      enabled: false,
      poll_interval_seconds: 43200,
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
  await page.goto(`${origin}/content/sources/new`);
  await page.getByLabel("RSS / Atom URL", { exact: false }).fill(feed);
  const retry = page.getByRole("button", { name: "Retry with solver" });
  await retry.waitFor();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].use_solver, undefined);
  await mkdir(`${root}/reports/source-solver`, { recursive: true });
  await page.screenshot({
    path: `${root}/reports/source-solver/explicit-retry.png`,
    fullPage: true,
  });
  await retry.click();
  await page.getByLabel("Name", { exact: true }).evaluate(
    (input) =>
      new Promise((resolve) => {
        const check = () =>
          input.value === "Engineering feed" ? resolve() : setTimeout(check, 50);
        check();
      }),
  );
  assert.equal(requests[1].use_solver, true);
  await page.getByLabel("RSS / Atom URL", { exact: false }).fill(feed + "-other");
  await retry.waitFor();
  assert.equal(requests[2].use_solver, undefined);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${root}/reports/source-solver/mobile-retry.png`, fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.goto(`${origin}/content/sources/source-1/edit`);
  const lookup = page.getByRole("button", { name: "Fetch source details" });
  await lookup.waitFor();
  const beforeEdit = requests.length;
  await lookup.click();
  await retry.waitFor();
  assert.equal(requests[beforeEdit].source_id, "source-1");
  assert.equal(requests[beforeEdit].use_solver, undefined);
  await retry.click();
  await retry.waitFor({ state: "hidden" });
  assert.equal(requests.at(-1).use_solver, true);
  assert.equal(await page.getByRole("textbox", { name: /^Name/ }).inputValue(), "Existing source");
  assert.deepEqual(errors, []);
  console.log(
    "Source solver browser checks passed: explicit opt-in, filled metadata, reset, edit preservation, mobile layout.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}
