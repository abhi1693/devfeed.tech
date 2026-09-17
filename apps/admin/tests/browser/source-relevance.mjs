import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. The API is a disposable local fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const assessment = {
  version: "source-relevance-v5",
  relevance: "relevant",
  confidence: 0.93,
  approval_supported: true,
  rejection_supported: false,
  reason:
    "Developer-focused engineering leadership, AI engineering, and engineering-role content clearly predominates.",
  checked_at: "2026-09-17T08:22:06.201584+00:00",
  model: "gpt-5.6-luna",
  sample: [
    "How to Deal With Company Politics as an Engineering Leader",
    "How to Become a Great Coach and Mentor",
    "How to Grow Past Senior Engineer",
    "What Elite Engineering Teams Do Differently in the Age of AI",
    "The Player-Coach Role is Becoming Increasingly Harder",
    "How Redis Builds AI-Native Engineering Teams",
    "Good Culture is the Biggest Productivity Hack, Not AI",
    "How Shutterstock Builds AI-Native Engineering Teams",
    "How to Tell if Your Manager is Actually Good?",
    "Inside OpenAI’s Forward Deployed Engineer Role",
  ].map((title, index) => ({ index, title })),
};
assessment.entries = assessment.sample.map(({ index, title }) => ({
  index,
  relevance: [1, 6, 8].includes(index) ? "uncertain" : "relevant",
  evidence: [1, 6, 8].includes(index) ? "" : title,
}));
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
  else if (path.endsWith("/sources/source-1"))
    body = {
      id: "source-1",
      name: "Engineering Leadership",
      slug: "engineering-leadership",
      feed_url: "https://newsletter.eng-leadership.com/feed",
      source_type: "publisher",
      enabled: true,
      poll_interval_seconds: 43200,
      approval_status: "approved",
      relevance_assessment: assessment,
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
  const tabs = page.getByRole("navigation", { name: "Object sections" });
  await tabs.waitFor();
  assert.deepEqual(await tabs.getByRole("link").allTextContents(), [
    "Details",
    "Relevance",
    "Related objects",
    "History",
  ]);
  assert.equal(
    await page.getByRole("heading", { name: "Evidence supports automatic approval" }).count(),
    0,
  );
  await tabs.getByRole("link", { name: "Relevance", exact: true }).click();
  await page.waitForURL("**/source-1/relevance");
  await page.getByRole("heading", { name: "Evidence supports automatic approval" }).waitFor();
  await page.reload();
  await page.getByRole("heading", { name: "Evidence supports automatic approval" }).waitFor();
  const articles = page.getByRole("list", { name: "Sampled articles" });
  assert.equal(await articles.getByRole("listitem").count(), 5);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page
    .locator("summary")
    .getByText("Inside OpenAI’s Forward Deployed Engineer Role", { exact: true })
    .waitFor();
  await page.getByRole("combobox", { name: "Filter article evidence" }).selectOption("uncertain");
  assert.equal(await articles.getByRole("listitem").count(), 3);
  await page.getByRole("combobox", { name: "Filter article evidence" }).selectOption("all");
  assert.match(await page.locator("body").innerText(), /Evidence supports automatic approval/);
  const evidence = page
    .locator("details")
    .filter({
      has: page.getByText("How to Deal With Company Politics as an Engineering Leader", {
        exact: true,
      }),
    })
    .first();
  await evidence.locator("summary").click();
  await evidence.getByText("Quoted evidence", { exact: true }).waitFor();
  await mkdir(`${root}/reports/source-relevance`, { recursive: true });
  await page.screenshot({ path: `${root}/reports/source-relevance/desktop.png`, fullPage: true });
  await page.screenshot({ path: `${root}/reports/source-relevance/preview.png` });
  const details = page
    .locator("details")
    .filter({ has: page.locator("summary", { hasText: "Assessment details" }) });
  assert.equal(await details.getAttribute("open"), null);
  await details.locator("summary").click();
  await page.getByText("source-relevance-v5", { exact: true }).waitFor();
  await details.locator("summary").click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${root}/reports/source-relevance/mobile.png`, fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await tabs.getByRole("link", { name: "Details", exact: true }).click();
  await page.waitForURL("**/sources/source-1");
  assert.equal(
    await page.getByRole("heading", { name: "Evidence supports automatic approval" }).count(),
    0,
  );
  assert.deepEqual(errors, []);
  console.log(
    "Source relevance browser checks passed: tab navigation, direct URL, pagination, filters, evidence disclosure, metadata, mobile layout, no runtime errors.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}
