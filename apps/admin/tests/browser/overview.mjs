import { checkAppIcons } from "../../../../scripts/testing/app-icons.mjs";
import { checkNonceCsp } from "../../../../scripts/testing/nonce-csp.mjs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. All API traffic stays on disposable localhost servers.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const requests = [];
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  let body = {};
  if (url.pathname.endsWith("/auth/sso/logout")) {
    assert.equal(req.method, "POST");
    assert.equal(req.headers["x-csrf-token"], "fixture");
    body = { url: "https://identity.example/end-session?state=admin" };
  } else if (url.pathname.endsWith("/auth/me"))
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
      defaults: { refresh_seconds: 0 },
    };
  else if (url.pathname.endsWith("/ai/connection"))
    body = {
      state: "connected",
      message: "Connected",
      email: "reviewer@example.com",
      model: "legacy-model",
      quota: [{ used_percent: 5, window_minutes: 10080, resets_at: "2026-09-20T21:19:48Z" }],
    };
  else if (url.pathname === "/v1/admin/articles")
    body = { items: [], total: 0, offset: 0, limit: 25 };
  else if (url.pathname.startsWith("/v1/admin/overview/")) requests.push(url.pathname);

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
  await context.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__cspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
    });
  });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(origin);
  await page.waitForURL(`${origin}/content/articles`);
  await page.getByRole("heading", { name: "Articles", exact: true }).waitFor();
  await checkAppIcons(page);
  const sidebar = page.getByRole("navigation", { name: "Administration" });
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  assert.equal(
    await sidebar.getByRole("link", { name: "Articles", exact: true }).getAttribute("aria-current"),
    "page",
  );
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  assert.equal(await page.getByRole("link", { name: "Search analytics" }).count(), 0);
  assert.equal(await page.getByRole("button", { name: /^Notifications/ }).count(), 0);
  await page.getByRole("button", { name: "AI connection: AI connected" }).click();
  const connection = page.getByRole("dialog", { name: "AI connection" });
  await connection.getByRole("progressbar", { name: "Weekly quota" }).waitFor();
  assert.equal(await connection.getByText("5% used").count(), 1);
  assert.equal(await connection.getByText("Model", { exact: true }).count(), 0);
  await connection.screenshot({ path: `${output}/ai-quota.png` });
  await page.keyboard.press("Escape");
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  assert.deepEqual(requests, []);
  assert.deepEqual(errors, []);
  await page.close();
  await checkNonceCsp(context, `${origin}/content/articles`);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify({ passed: true, landingPage: "/content/articles" }),
  );
} catch (error) {
  console.error(logs.slice(-4000));
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  fixture.close();
}
