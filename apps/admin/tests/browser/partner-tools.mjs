import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const product = {
  id: "11111111-1111-1111-1111-111111111111",
  provider: "nick-launches",
  external_id: "api-checker",
  name: "API Checker",
  description: "Checks OpenAPI specifications for breaking changes before deployment.",
  product_url: "https://checker.example/",
  listing_url: "https://nicklaunches.com/products/api-checker/",
  pricing: "free",
  technologies: ["OpenAPI"],
  attribution: "Via Nick Launches",
  evidence: [
    {
      url: "https://checker.example/docs",
      quote: "Detect breaking changes between OpenAPI specifications.",
      capability: "Check API compatibility in CI.",
    },
  ],
  status: "pending",
  eligible: false,
  revision: 1,
  verified_at: null,
  updated_at: new Date().toISOString(),
  reviews: [],
};
const articleId = "22222222-2222-2222-2222-222222222222";
const run = {
  id: "33333333-3333-3333-3333-333333333333",
  product_id: product.id,
  created_at: new Date().toISOString(),
  finished_at: new Date().toISOString(),
  status: "succeeded",
  current: true,
  error: null,
  reviews: [],
  snapshot: {
    version: "1",
    product: { ...product },
    articles: [
      {
        id: articleId,
        title: "OpenAPI compatibility in CI",
        text: "Detect breaking OpenAPI changes before merging a pull request.",
        content_type: "tutorial",
        published: true,
        editorial_revision: 0,
      },
    ],
  },
  result: {
    decisions: [
      {
        article_id: articleId,
        relevant: true,
        reason: "This tool checks the API compatibility task discussed in the tutorial.",
        article_quote: "Detect breaking OpenAPI changes before merging a pull request.",
        evidence_index: 0,
        technology: "OpenAPI",
      },
    ],
  },
};
let runs = [];
const mutations = [];
const fixture = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = {};
  if (req.method === "POST") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    body = JSON.parse(raw || "{}");
    mutations.push({ path, body, csrf: req.headers["x-csrf-token"] });
  }
  let result;
  if (path.endsWith("/auth/me"))
    result = {
      subject: "fixture",
      name: "Partner reviewer",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (path.endsWith("/settings"))
    result = { appearance: { theme: "dark" }, defaults: { refresh_seconds: 0, overview_days: 30 } };
  else if (path.endsWith("/notifications/config")) result = { enabled: false };
  else if (path.endsWith("/ai/connection"))
    result = { state: "connected", message: "Connected", model: "fixture", quota: [] };
  else if (path.endsWith("/partner-tools"))
    result = { items: [product], total: 1, limit: 25, offset: 0 };
  else if (path.endsWith(`/evaluations/${run.id}/review`)) {
    run.reviews.push(body);
    result = run;
  } else if (path.endsWith("/review")) {
    assert.equal(body.evidence_checked, true);
    assert.equal(body.display_rights_confirmed, true);
    Object.assign(product, {
      status: body.status,
      eligible: true,
      verified_at: new Date().toISOString(),
    });
    result = product;
  } else if (path.endsWith("/evaluations")) {
    if (req.method === "POST") {
      runs = [run];
      result = run;
    } else result = runs;
  } else {
    res.writeHead(404);
    res.end("{}");
    return;
  }
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(result));
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
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
let browser;
const output = `${root}/reports/admin-partner-tools`;
await mkdir(output, { recursive: true });
try {
  for (let i = 0; i < 100; i++) {
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
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/partner-tools`);
  await page.getByRole("button", { name: /API Checker/ }).click();
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  assert.equal(await approve.isDisabled(), true);
  assert.equal(
    await page.getByRole("button", { name: "Run private evaluation" }).isDisabled(),
    true,
  );
  await page
    .getByLabel("Review note", { exact: false })
    .fill("Checked official documentation and display permission.");
  await page.getByLabel(/I checked the capability/).check();
  assert.equal(await approve.isDisabled(), true);
  await page.getByLabel(/Permission to display/).check();
  await approve.click();
  await page.getByRole("status").filter({ hasText: "Reader visibility remains off" }).waitFor();
  await page.getByRole("button", { name: "Run private evaluation" }).click();
  await page.getByText("1 proposed matches · 0 no-match results").waitFor();
  await page
    .getByLabel(/Assessment note for/)
    .fill("Specific task and evidence support this match.");
  await page.getByRole("button", { name: "Agree with assessment", exact: true }).click();
  await page.getByText("Proposed match · Review accepted").waitFor();
  assert.equal(mutations.length, 3);
  assert.ok(mutations.every((entry) => entry.csrf === "fixture"));
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "Edit product" }).click();
  await page.getByLabel("Product name", { exact: false }).waitFor();
  assert.equal(await page.getByLabel("External ID", { exact: false }).isDisabled(), true);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.deepEqual(errors, []);
  console.log(`Partner catalog browser workflow passed. Screenshots: ${output}`);
} catch (error) {
  console.error(logs.slice(-4000));
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  fixture.close();
}
