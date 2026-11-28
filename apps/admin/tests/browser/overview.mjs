import { checkAppIcons } from "../../../../scripts/testing/app-icons.mjs";
import { checkNonceCsp } from "../../../../scripts/testing/nonce-csp.mjs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { notificationFixture } from "../../../web/tests/browser/reader-interactions.mjs";

// Run after npm run admin:build. All API traffic stays on disposable localhost servers.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const requests = [];
const inboxPath = "/v1/admin/notifications/chimely/v1/inbox";
const inboxItems = notificationFixture();
const inboxItem = inboxItems.items[0];
inboxItem.category = "jobs.ingestion.success";
inboxItem.payload = {
  title: "Feed import completed",
  body: "Imported articles are ready for review.",
  severity: "success",
};
let inboxSeen = false;
const inboxRequests = [];
const inboxCounts = () => ({ unread: Number(!inboxItem.read), unseen: Number(!inboxSeen) });
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  let body = {};
  if (url.pathname.startsWith(inboxPath + "/")) {
    const request = {
      method: req.method,
      path: url.pathname,
      cookie: req.headers.cookie,
      origin: req.headers.origin,
      csrfToken: req.headers["x-csrf-token"],
    };
    inboxRequests.push(request);
    if (req.method === "GET" && url.pathname === inboxPath + "/stream") {
      request.streamClosed = false;
      res.once("close", () => (request.streamClosed = true));
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
      });
      res.write(": connected\n\n");
      res.write('id: admin-fixture-1\nevent: hint\ndata: {"revision":1}\n\n');
      // Keep the response open: the browser must receive the hint before EOF.
      return;
    }
    if (req.method === "GET" && url.pathname === inboxPath + "/items") body = inboxItems;
    else if (req.method === "GET" && url.pathname === inboxPath + "/counts") body = inboxCounts();
    else if (req.method === "GET" && url.pathname === inboxPath + "/preferences")
      body = { preferences: [] };
    else if (req.method === "POST" && url.pathname === inboxPath + "/seen-all") {
      inboxSeen = true;
      body = inboxCounts();
    } else if (
      req.method === "POST" &&
      url.pathname === `${inboxPath}/notifications/${inboxItem.id}/read`
    ) {
      inboxItem.read = true;
      body = inboxCounts();
    } else {
      res.statusCode = 404;
      body = { detail: "Unknown fixture inbox operation" };
    }
  } else if (url.pathname.endsWith("/auth/sso/logout")) {
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
  else if (url.pathname.endsWith("/notifications/config"))
    body = { enabled: true, environment: "admin-fixture", subscriber_id: "admin_fixture" };
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
    window.__inboxStreams = [];
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url, options) {
        super(url, options);
        const observed = { url: String(url), opened: false, hints: [] };
        window.__inboxStreams.push(observed);
        this.addEventListener("open", () => (observed.opened = true));
        this.addEventListener("hint", (event) =>
          observed.hints.push({ data: event.data, lastEventId: event.lastEventId }),
        );
      }
    };
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
  await page.waitForFunction(() =>
    window.__inboxStreams.some(
      (stream) =>
        stream.url.includes("/api/v1/admin/notifications/chimely/v1/inbox/stream") &&
        stream.opened &&
        stream.hints.some(
          (hint) => hint.lastEventId === "admin-fixture-1" && hint.data === '{"revision":1}',
        ),
    ),
  );
  assert.ok(
    inboxRequests.some(
      (request) => request.path === inboxPath + "/stream" && !request.streamClosed,
    ),
    "Native EventSource receives the hint while the upstream response remains open",
  );
  const bell = page.getByRole("button", { name: "Notifications, 1 new", exact: true });
  await bell.waitFor();
  const seenResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api" + inboxPath + "/seen-all" &&
      response.request().method() === "POST",
  );
  await bell.click();
  assert.equal((await seenResponse).status(), 200);
  const notice = page.getByText("Feed import completed", { exact: true });
  await notice.waitFor();
  await page.locator(".devfeed-notification.chimely-item-unread").waitFor();
  const readPath = `${inboxPath}/notifications/${inboxItem.id}/read`;
  const readResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api" + readPath &&
      response.request().method() === "POST",
  );
  await notice.click();
  assert.equal((await readResponse).status(), 200);
  await page.locator(".devfeed-notification.chimely-item-unread").waitFor({ state: "hidden" });
  assert.equal(await notice.isVisible(), true);
  for (const path of [inboxPath + "/seen-all", readPath]) {
    const mutation = inboxRequests.find(
      (request) => request.method === "POST" && request.path === path,
    );
    assert.ok(mutation, `The real inbox SDK sent POST ${path}`);
    assert.equal(mutation.csrfToken, "fixture");
    assert.equal(mutation.origin, origin);
    assert.match(mutation.cookie, /(?:^|;\s*)devfeed_admin_session=fixture(?:;|$)/);
  }
  for (const suffix of ["preferences", "items", "counts", "stream"])
    assert.ok(
      inboxRequests.some(
        (request) => request.method === "GET" && request.path === `${inboxPath}/${suffix}`,
      ),
      `The real inbox SDK requested ${suffix} through the native gateway operation`,
    );
  // Closing the open panel must not depend on concurrent badge/count refreshes.
  await page.getByRole("button", { name: /^Notifications(?:, 1 new)?$/, expanded: true }).click();
  await page.locator(".devfeed-inbox-panel").waitFor({ state: "hidden" });
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
