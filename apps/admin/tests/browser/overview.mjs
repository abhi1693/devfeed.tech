import { checkAppIcons } from "../../../../scripts/testing/app-icons.mjs";
import { checkNonceCsp } from "../../../../scripts/testing/nonce-csp.mjs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import { checkBrowserIcons } from "../../../web/tests/browser/article-first.mjs";
import { notificationFixture } from "../../../web/tests/browser/reader-interactions.mjs";

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
      defaults: { refresh_seconds: mode.startsWith("live") ? 5 : 0, overview_days: 30 },
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
  const scripts = new Set();
  page.on("request", (request) => {
    if (request.resourceType() === "script") scripts.add(request.url());
  });
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
  await checkAppIcons(page);
  const initialSidebarBounds = [];
  for (const height of [1080, 600]) {
    await page.setViewportSize({ width: 1920, height });
    await page.locator("#admin-sidebar").waitFor({ state: "visible" });
    await page.waitForFunction(() => {
      const bounds = document.querySelector("#admin-sidebar").getBoundingClientRect();
      return bounds.width > 0 && bounds.height > 0 && Math.abs(bounds.bottom - innerHeight) <= 1;
    });
    const geometry = await page.locator("#admin-sidebar").evaluate((node) => {
      const aside = node.getBoundingClientRect();
      const toggle = node.querySelector(".sidebar-toggle").getBoundingClientRect();
      return {
        top: aside.top,
        width: aside.width,
        height: aside.height,
        bottom: aside.bottom,
        toggleTop: toggle.top,
        toggleBottom: toggle.bottom,
        viewportHeight: innerHeight,
        scrollY,
      };
    });
    initialSidebarBounds.push(geometry);
    await page.screenshot({ path: `${output}/sidebar-initial-${height}.png` });
  }
  console.log("Initial sidebar geometry:", JSON.stringify(initialSidebarBounds));
  for (const geometry of initialSidebarBounds) {
    assert.ok(
      geometry.width > 0 && geometry.height > 0,
      "The rendered sidebar has measurable bounds",
    );
    assert.equal(
      geometry.scrollY,
      0,
      "Initial sidebar measurement precedes any interaction or scroll",
    );
    assert.ok(
      geometry.toggleTop >= 0 && geometry.toggleBottom <= geometry.viewportHeight + 1,
      "Sidebar toggle is reachable before the static header scrolls away",
    );
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await checkBrowserIcons(page, true);
  const response = await page.request.get(origin);
  assert.match(response.headers()["content-security-policy"], /script-src [^;]*'nonce-[^']+'/);
  assert.doesNotMatch(
    response.headers()["content-security-policy"],
    /script-src [^;]*'unsafe-inline'/,
  );
  assert.equal(response.headers()["strict-transport-security"], "max-age=31536000");
  assert.equal(await page.getByRole("link", { name: "Knowledge graph" }).count(), 0);
  const removed = await context.newPage();
  await removed.goto(`${origin}/knowledge/graph`);
  await removed.getByRole("heading", { name: "404" }).waitFor();
  await removed.close();
  console.log("Loaded", page.url());
  await settled();
  assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
  const initialScripts = scripts.size;
  const initialPanels = [...new Set(requests.map(({ panel }) => panel))];
  assert.ok(initialPanels.length < 16, `Initial panel requests: ${initialPanels.length}`);
  assert.ok(!initialPanels.includes("tokens-by-model"));
  for (const panel of await page.locator("[data-overview-panel]").all()) {
    await panel.scrollIntoViewIfNeeded();
    await panel.locator(".overview-panel-content").waitFor();
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await settled();
  assert.ok(scripts.size > initialScripts, "Chart modules should load on demand while scrolling");
  assert.equal(await page.locator("main h2").count(), 6);
  assert.equal(await page.getByRole("navigation", { name: "Overview sections" }).count(), 1);
  assert.ok(maximum <= 4, `Concurrent overview requests: ${maximum}`);
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
  await page.getByRole("button", { name: "Notifications", exact: true }).click();
  await page.locator(".devfeed-inbox-panel").waitFor({ state: "hidden" });
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
  const sidebar = page.locator("#admin-sidebar");
  const administration = sidebar.getByRole("navigation", { name: "Administration", exact: true });
  const waitForSidebarWidth = (target, width) =>
    target.waitForFunction(
      (expected) =>
        Math.abs(
          document.querySelector("#admin-sidebar").getBoundingClientRect().width - expected,
        ) <= 1,
      width,
    );
  await waitForSidebarWidth(page, 76);
  assert.equal(await sidebar.getAttribute("data-expanded"), "false");
  const navigationIcons = await administration
    .getByRole("link")
    .evaluateAll((links) => links.map((link) => link.querySelector("svg")?.getAttribute("class")));
  assert.equal(
    new Set(navigationIcons).size,
    navigationIcons.length,
    "Collapsed administration destinations have distinct icons",
  );
  assert.equal(
    await administration
      .getByRole("link", { name: "Overview", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  assert.equal(
    await administration.getByRole("link", { name: "Products", exact: true }).getAttribute("title"),
    "Products",
  );
  await page.screenshot({ path: `${output}/sidebar-collapsed-desktop.png` });
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).focus();
  await page.keyboard.press("Enter");
  await waitForSidebarWidth(page, 232);
  assert.equal(
    await page
      .getByRole("button", { name: "Collapse sidebar", exact: true })
      .evaluate((node) => document.activeElement === node),
    true,
  );
  await page.screenshot({ path: `${output}/sidebar-expanded-desktop.png` });
  const restored = await context.newPage();
  await restored.goto(origin);
  await waitForSidebarWidth(restored, 232);
  assert.equal(await restored.locator("#admin-sidebar").getAttribute("data-expanded"), "true");
  await restored.close();
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  await waitForSidebarWidth(page, 76);
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).focus();
  await page.keyboard.press("Space");
  await waitForSidebarWidth(page, 232);
  // The header scrolls away; the sticky sidebar must still fill the viewport.
  for (const height of [1080, 600]) {
    await page.setViewportSize({ width: 1920, height });
    await page.evaluate(() => window.scrollTo(0, 1200));
    await page.waitForFunction(() => {
      const bounds = document.querySelector("#admin-sidebar").getBoundingClientRect();
      return Math.abs(bounds.top) <= 1 && Math.abs(bounds.bottom - innerHeight) <= 1;
    });
    const bounds = await sidebar.boundingBox();
    assert.ok(Math.abs(bounds.y) <= 1, "Sidebar should stick to the viewport top");
    assert.ok(
      Math.abs(bounds.y + bounds.height - height) <= 1,
      "Sidebar should reach the viewport bottom",
    );
    const content = sidebar.locator(".shared-sidebar-content");
    await content.evaluate((node) => (node.scrollTop = node.scrollHeight));
    const last = await sidebar.getByRole("link").last().boundingBox();
    assert.ok(last.y >= 0 && last.y + last.height <= height, "Last navigation item is reachable");
    const toggle = sidebar.getByRole("button", { name: "Collapse sidebar", exact: true });
    const toggleBounds = await toggle.boundingBox();
    assert.ok(
      toggleBounds.y >= 0 && toggleBounds.y + toggleBounds.height <= height,
      "Sidebar toggle stays visible outside the scrolling navigation",
    );
    await sidebar.getByRole("link").last().focus();
    await page.keyboard.press("Tab");
    assert.equal(
      await toggle.evaluate((node) => document.activeElement === node),
      true,
      "Keyboard navigation reaches the pinned toggle after the last link",
    );
    await page.screenshot({ path: `${output}/sidebar-scrolled-${height}.png` });
    await content.evaluate((node) => (node.scrollTop = 0));
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
  const mobileNavigation = page.getByRole("button", { name: "Navigation", exact: true });
  assert.equal(await mobileNavigation.getAttribute("aria-expanded"), "false");
  await mobileNavigation.click();
  await administration.waitFor({ state: "visible" });
  assert.equal(await mobileNavigation.getAttribute("aria-expanded"), "true");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: `${output}/sidebar-mobile-open.png` });
  await page.keyboard.press("Escape");
  assert.equal(await mobileNavigation.getAttribute("aria-expanded"), "false");
  assert.equal(await mobileNavigation.evaluate((node) => document.activeElement === node), true);
  await administration.waitFor({ state: "hidden" });
  await mobileNavigation.click();
  await administration.getByRole("link", { name: "Overview", exact: true }).click();
  await administration.waitFor({ state: "hidden" });
  assert.equal(await mobileNavigation.getAttribute("aria-expanded"), "false");
  await page.screenshot({ path: `${output}/sidebar-mobile-closed.png` });
  assert.equal(await page.getByRole("button", { name: "Refresh all" }).count(), 0);
  assert.equal(await page.getByRole("status", { name: "Overview refresh status" }).count(), 0);
  mode = "diagnostic-failure";
  await page.getByRole("button", { name: "7 days", exact: true }).click();
  await page.locator("#overview-panel-tokens-by-model").scrollIntoViewIfNeeded();
  await page.locator("#overview-panel-tokens-by-model").getByRole("alert").waitFor();
  mode = "failure";
  await page.getByRole("button", { name: "30 days", exact: true }).click();
  await page.locator("#overview-panel-publications").scrollIntoViewIfNeeded();
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
  await page.locator("#overview-panel-tokens-by-model").scrollIntoViewIfNeeded();
  await settled();
  const workloadRequests = requests.filter(({ panel }) => panel === "workload").length;
  await page.waitForTimeout(6000);
  assert.equal(
    requests.filter(({ panel }) => panel === "workload").length,
    workloadRequests,
    "Offscreen workload must stop polling",
  );
  await workload.scrollIntoViewIfNeeded();
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
  const maximumConcurrentPanelRequests = maximum;
  await page.close();
  mode = "populated";
  // Separate nonce-check navigations must not affect overview request measurements.
  await checkNonceCsp(context, origin);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        maximumConcurrentPanelRequests,
        initialPanels,
        notificationRequests: inboxRequests,
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
          "shared collapsed rail, persisted expansion, active routes and keyboard toggle",
          "pinned toggle after scrolling long navigation, mobile link/Escape dismissal",
          "live polling, deltas, reconnect and recovery",
          "real inbox SDK bell, items, counts, seen and read through native gateway operations",
          "native EventSource receives an unbuffered hint before the stream closes",
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
