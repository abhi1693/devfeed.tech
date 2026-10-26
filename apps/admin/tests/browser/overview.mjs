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
const { populatedCharts, emptyCharts, charts, reportForRange } = await import(
  `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`
);
const chartSections = {
  attention: "operations",
  "publication-rate": "publishing",
  "publication-latency": "publishing",
  "audience-activity": "audience",
  personalization: "audience",
  "queue-pressure": "processing",
  throughput: "processing",
  "job-outcomes": "processing",
  "ai-activity": "inference",
  "ai-breakdown": "inference",
  "source-output": "coverage",
  "topic-coverage": "coverage",
};
const sections = [...new Set(Object.values(chartSections))];
// Several real buckets make the shared chart styling visible in browser artifacts.
const buckets = [23, 24, 25, 26].map((day) => `2026-10-${day}T00:00:00Z`);
populatedCharts["publication-rate"].data.activity = buckets.map((bucket, index) => ({
  bucket,
  published: [6, 9, 12, 15][index],
  automated: [5, 7, 10, 13][index],
}));
populatedCharts["publication-latency"].data.activity = buckets.map((bucket, index) => ({
  bucket,
  p50_seconds: [180, 120, 90, 60][index],
  p95_seconds: [600, 480, 360, 240][index],
}));
populatedCharts["audience-activity"].data.activity = buckets.map((bucket, index) => ({
  bucket,
  opens: [10, 20, 30, 40][index],
  estimated_readers: [8, 14, 20, 28][index],
  new_accounts: 1,
}));
populatedCharts.throughput.data.throughput = buckets.map((bucket, index) => ({
  bucket,
  articles_published: [6, 9, 12, 15][index],
  article_analyses: [8, 10, 14, 18][index],
  topic_decisions: 1,
}));
populatedCharts["ai-activity"].data.activity = buckets.map((bucket, index) => ({
  bucket,
  calls: 2,
  failed: Number(index === 3),
  unreported: Number(index === 0 || index === 3),
  input_tokens: [300, 400, 500, 600][index],
  output_tokens: [60, 80, 120, 140][index],
  cached_input_tokens: [20, 40, 60, 80][index],
  reasoning_tokens: [10, 20, 30, 40][index],
  web_searches: Number(index > 0),
  p50_ms: [2500, 3000, 2400, 2000][index],
  p95_ms: [9000, 10000, 8500, 8000][index],
}));
let mode = "populated",
  theme = "dark",
  active = 0,
  maximum = 0;
const requests = [];
const recoveries = [];
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
      appearance: { theme },
      defaults: { refresh_seconds: mode.startsWith("live") ? 5 : 0 },
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
  else if (
    url.pathname === "/v1/admin/automation/articles/00000000-0000-0000-0000-000000000003/analyze"
  ) {
    assert.equal(req.method, "POST");
    let raw = "";
    for await (const chunk of req) raw += chunk;
    recoveries.push({ body: JSON.parse(raw), csrf: req.headers["x-csrf-token"] });
    body = { status: "queued" };
  } else if (url.pathname.startsWith("/v1/admin/overview/charts/")) {
    const chart = url.pathname.split("/").at(-1);
    assert.ok(charts.includes(chart), `Unknown Overview chart ${chart}`);
    const section = chartSections[chart];
    const request = {
      chart,
      section,
      from: url.searchParams.get("from"),
      to: url.searchParams.get("to"),
      startedAt: Date.now(),
      completed: false,
    };
    assert.deepEqual(
      [...url.searchParams.keys()].sort(),
      chart === "attention" ? [] : ["from", "to"],
      "Chart requests send only Grafana from/to parameters",
    );
    requests.push(request);
    res.once("close", () => {
      request.closed = true;
    });
    active++;
    maximum = Math.max(maximum, active);
    let released = false;
    const release = () => {
      if (!released) active--;
      released = true;
    };
    res.once("close", release);
    await new Promise((resolve) => setTimeout(resolve, mode === "loading" ? 500 : 20));
    release();
    request.completed = true;
    body = structuredClone((mode === "empty" ? emptyCharts : populatedCharts)[chart]);
    body.generated_at = new Date(Date.now() - (mode === "stale" ? 600000 : 0)).toISOString();
    body.snapshot = {
      status: mode === "stale" ? "stale" : "fresh",
      generated_at: body.generated_at,
      generation: "fixture",
      lag_seconds: mode === "stale" ? 600 : 0,
    };
    body = reportForRange(body, { from: request.from ?? undefined, to: request.to ?? undefined });
    if (mode === "all-clear" && chart === "attention") {
      body.data.reviews = ["articles", "sources", "topics", "relationships"].map((kind) => ({
        kind,
        count: 0,
      }));
      body.data.blockers = [];
      body.data.source_failures = 0;
    }
    if (chart === "attention" && body.data.blockers[0])
      body.data.blockers[0].targets[0].id = "00000000-0000-0000-0000-000000000003";
    if (chart === "queue-pressure" && mode === "live-changed") {
      const analysis = body.data.queues.find((row) => row.kind === "analysis");
      analysis.queued = 8976;
      analysis.running = 3;
    }
    if (
      (mode === "failure" && chart === "publication-rate") ||
      (mode === "diagnostic-failure" && chart === "ai-activity") ||
      (mode === "live-failure" && chart === "queue-pressure")
    ) {
      res.statusCode = 500;
      body = { detail: "Fixture chart failure" };
    }
    if (mode === "preparing" && chart === "publication-rate") {
      res.statusCode = 503;
      body = { detail: { code: "report_refreshing", message: "Report is being prepared" } };
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
        const panels = document.querySelectorAll("[data-overview-chart]");
        return (
          panels.length === 12 &&
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
  const initialSections = [...new Set(requests.map(({ section }) => section))];
  const initialCharts = [...new Set(requests.map(({ chart }) => chart))];
  assert.ok(initialSections.length <= 6, `Initially loaded sections: ${initialSections.length}`);
  for (const section of await page.locator("[data-overview-chart]").all()) {
    await section.scrollIntoViewIfNeeded();
    await section.locator("[data-overview-card]").first().waitFor();
  }
  await settled();
  assert.deepEqual(
    [...new Set(requests.map(({ section }) => section))].sort(),
    [...sections].sort(),
  );
  assert.equal(requests.length, 12, `Each chart owns one request: ${JSON.stringify(requests)}`);
  assert.equal(await page.locator("[data-overview-card]").count(), 12);
  assert.deepEqual([...new Set(requests.map(({ chart }) => chart))].sort(), [...charts].sort());
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.setViewportSize({ width: 1743, height: 786 });
  const publicationChart = page.getByRole("figure", { name: "Publication output chart" });
  await publicationChart.waitFor();
  const firstChart = await publicationChart.boundingBox();
  assert.ok(
    firstChart.y < 300 && firstChart.y + firstChart.height < 786,
    "The first viewport contains actual publishing charts",
  );
  await page.screenshot({ path: `${output}/desktop-first-1743x786.png` });
  await page.getByRole("button", { name: "About Publication output", exact: true }).click();
  await page.getByRole("tooltip").waitFor();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1920, height: 1080 });
  assert.equal(await page.locator("[data-overview-section]").count(), 6);
  assert.equal(await page.getByRole("navigation", { name: "Overview sections" }).count(), 0);
  assert.ok(maximum <= 4, `Concurrent overview requests: ${maximum}`);
  const initialMaximumConcurrentChartRequests = maximum;
  const beforeRecovery = requests.length;
  await page.getByRole("button", { name: "Analyze again", exact: true }).click();
  await page.getByText("Recovery job queued", { exact: true }).waitFor();
  await settled();
  assert.deepEqual(recoveries, [{ body: { expected_revision: 7 }, csrf: "fixture" }]);
  assert.equal(requests.length, beforeRecovery + 1);
  assert.equal(
    requests.at(-1).section,
    "operations",
    "Recovery refreshes only the attention chart",
  );
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
  await page
    .locator('[data-overview-card="queue-pressure"]')
    .screenshot({ path: `${output}/workload.png` });
  await page.locator("#processing").screenshot({ path: `${output}/processing.png` });
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
  const audience = page.locator("#audience");
  await audience.scrollIntoViewIfNeeded();
  const beforeChoices = requests.length;
  const choose = async (scope, label, option) => {
    await scope.getByRole("combobox", { name: label }).click();
    await page.getByRole("option", { name: option, exact: true }).click();
  };
  await choose(audience, "Audience metric", "Estimated readers");
  await choose(audience, "Audience metric", "New accounts");
  const inference = page.locator("#inference");
  await inference.scrollIntoViewIfNeeded();
  await choose(inference, "AI activity metric", "Calls and failures");
  await choose(inference, "AI usage breakdown", "By model");
  assert.equal(requests.length, beforeChoices, "Chart selectors reuse their own chart data");
  assert.equal(await page.locator("main details").count(), 0);
  for (const card of await page.locator("[data-overview-card]").all())
    assert.ok(await card.isVisible(), "All twelve charts are displayed without expansion");
  await page.setViewportSize({ width: 390, height: 844 });
  // Responsive charts resize through ResizeObserver after the viewport changes.
  await page
    .waitForFunction(() => document.documentElement.scrollWidth <= innerWidth, undefined, {
      timeout: 5000,
    })
    .catch(async (failure) => {
      console.error(
        "Mobile overflow",
        JSON.stringify(
          await page.evaluate(() => ({
            width: innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            elements: [...document.querySelectorAll("main *")]
              .filter((node) => node.getBoundingClientRect().right > innerWidth)
              .slice(0, 30)
              .map((node) => ({
                tag: node.tagName,
                cls: node.getAttribute("class"),
                figure: node.closest("figure")?.getAttribute("aria-label"),
                width: node.getBoundingClientRect().width,
                text: node.textContent.slice(0, 80),
              })),
          })),
        ),
      );
      await page.screenshot({ path: `${output}/mobile-overflow.png`, fullPage: true });
      throw failure;
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
  const chooseRange = async (label) => {
    await page.getByRole("button", { name: "Chart date range", exact: true }).click();
    await page.getByRole("button", { name: label, exact: true }).click();
  };
  const operationsBeforeDates = requests.filter(({ section }) => section === "operations").length;
  await page.getByRole("button", { name: "Chart date range", exact: true }).click();
  for (const label of [
    "Today",
    "Yesterday",
    "Last 3 days",
    "Last 7 days",
    "Last 14 days",
    "Last 30 days",
    "Last 3 months",
    "Last 6 months",
    "Last 9 months",
    "This year",
    "Last year",
  ])
    assert.equal(await page.getByRole("button", { name: label, exact: true }).count(), 1);
  await page.screenshot({ path: `${output}/date-range-picker-mobile.png` });
  await page.keyboard.press("Escape");
  await chooseRange("Last 3 months");
  await settled();
  assert.ok(requests.some(({ from, to }) => from === "now-3M" && to === "now"));
  assert.equal(new URL(page.url()).searchParams.get("from"), "now-3M");
  assert.equal(new URL(page.url()).searchParams.get("to"), "now");
  await chooseRange("Last 30 days");
  await settled();
  const initialWindow = reportForRange(populatedCharts["publication-rate"]);
  const earlier = (value) => new Date(Date.parse(value) - 86_400_000).toISOString().slice(0, 10);
  const customFrom = earlier(initialWindow.start);
  const customTo = earlier(initialWindow.end);
  await page.getByRole("button", { name: "Chart date range", exact: true }).click();
  await page.getByRole("button", { name: /^From:/ }).click();
  await page.keyboard.press("ArrowLeft");
  await page.locator(`[role="grid"] button[data-date="${customFrom}"]`).click();
  await page.getByRole("button", { name: /^To:/ }).click();
  await page.keyboard.press("ArrowLeft");
  await page.locator(`[role="grid"] button[data-date="${customTo}"]`).click();
  await page.getByRole("button", { name: "Apply time range", exact: true }).click();
  await settled();
  assert.ok(
    requests.some(({ from, to }) => from === customFrom && to === customTo),
    "Custom dates use an inclusive absolute from/to identity",
  );
  assert.equal(new URL(page.url()).searchParams.get("from"), customFrom);
  assert.equal(new URL(page.url()).searchParams.get("to"), customTo);
  assert.equal(
    requests.filter(({ section }) => section === "operations").length,
    operationsBeforeDates,
    "Date changes keep the current Operations document",
  );
  const beforeDateReload = requests.length;
  const restoredChart = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/v1/admin/overview/charts/publication-rate" &&
      url.searchParams.get("from") === customFrom &&
      url.searchParams.get("to") === customTo &&
      response.status() === 200
    );
  });
  await page.reload();
  await page.locator("#publishing").scrollIntoViewIfNeeded();
  await restoredChart;
  await page.locator("#publishing [data-overview-card]").first().waitFor();
  await settled();
  const savedDateLabel = await page
    .getByRole("button", { name: "Chart date range", exact: true })
    .textContent();
  assert.ok(
    savedDateLabel.includes(customFrom) && savedDateLabel.includes(customTo),
    "Reload restores the shared historical window",
  );
  assert.ok(
    requests.slice(beforeDateReload).some(({ from, to }) => from === customFrom && to === customTo),
  );
  await chooseRange("Last 30 days");
  await settled();
  mode = "diagnostic-failure";
  await chooseRange("Last 7 days");
  await page.locator("#inference").scrollIntoViewIfNeeded();
  await page.locator("#inference").getByRole("alert").waitFor();
  assert.ok(await page.locator("#operations [data-overview-card]").count());
  mode = "populated";
  const beforeRetry = requests.length;
  const retried = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/admin/overview/charts/ai-activity" &&
      response.status() === 200,
  );
  await page.locator("#inference").getByRole("button", { name: "Try again", exact: true }).click();
  await retried;
  await page.waitForFunction(
    () => document.querySelector("#ai-activity").getAttribute("aria-busy") === "false",
  );
  await page.locator("#inference").getByRole("alert").waitFor({ state: "hidden" });
  assert.equal(requests.length, beforeRetry + 1);
  assert.equal(requests.at(-1).section, "inference");
  mode = "failure";
  await chooseRange("Last 30 days");
  await page.locator("#publishing").scrollIntoViewIfNeeded();
  await page.locator("#publishing").getByRole("alert").waitFor();
  await page.screenshot({ path: `${output}/partial-failure.png`, fullPage: true });
  mode = "preparing";
  const firstPreparingResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/admin/overview/charts/publication-rate" &&
      response.status() === 503,
  );
  await chooseRange("Last 3 months");
  const publicationSection = page.locator('[data-overview-chart="publication-rate"]');
  await publicationSection.scrollIntoViewIfNeeded();
  await firstPreparingResponse;
  await publicationSection
    .getByRole("status", { name: "Loading Publication output", exact: true })
    .waitFor();
  assert.equal(
    await publicationSection.getByRole("figure", { name: "Publication output chart" }).count(),
    0,
    "A new range shows an in-chart loader, not previous values",
  );
  assert.equal(await publicationSection.getByRole("alert").count(), 0);
  assert.equal(
    await publicationSection.getByRole("button", { name: "Try again", exact: true }).count(),
    0,
  );
  assert.equal(await page.getByText(/Loading range|Showing the previous range/).count(), 0);
  assert.equal(
    await page.getByRole("figure", { name: "Publication delay chart" }).count(),
    1,
    "The sibling chart has its own successful request",
  );
  const preparingReads = requests.filter(({ chart }) => chart === "publication-rate").length;
  await page.waitForTimeout(2500);
  assert.equal(
    requests.filter(({ chart }) => chart === "publication-rate").length,
    preparingReads,
    "Preparation checks wait five seconds",
  );
  await publicationSection.screenshot({ path: `${output}/preparing.png` });
  await publicationSection
    .getByText("Still preparing this chart", { exact: true })
    .waitFor({ timeout: 20000 });
  const delayedReads = requests.filter(({ chart }) => chart === "publication-rate").length;
  assert.equal(
    delayedReads,
    preparingReads + 2,
    "Initial completion checks run five seconds apart",
  );
  assert.equal(
    await publicationSection.getByRole("button", { name: "Try again", exact: true }).count(),
    0,
    "Preparing charts recover without a manual retry",
  );
  await publicationSection.screenshot({ path: `${output}/delayed-preparing.png` });
  await page.waitForTimeout(5500);
  assert.equal(
    requests.filter(({ chart }) => chart === "publication-rate").length,
    delayedReads,
    "Delayed preparation checks back off to fifteen seconds",
  );
  mode = "populated";
  await publicationSection
    .getByRole("figure", { name: "Publication output chart" })
    .waitFor({ timeout: 20000 });
  assert.equal(
    requests.filter(({ chart }) => chart === "publication-rate").length,
    delayedReads + 1,
    "The chart appears automatically after its delayed completion check",
  );
  mode = "preparing";
  await page.reload();
  await publicationSection
    .getByRole("status", { name: "Loading Publication output", exact: true })
    .waitFor();
  assert.equal(await publicationSection.getByRole("alert").count(), 0);
  assert.equal(
    await publicationSection.locator('[data-overview-card="publication-rate"]').count(),
    1,
  );
  await page.screenshot({ path: `${output}/cold-preparing.png` });
  mode = "populated";
  await publicationSection.getByRole("figure", { name: "Publication output chart" }).waitFor();
  mode = "loading";
  await chooseRange("Last 30 days");
  await publicationSection
    .getByRole("status", { name: "Loading Publication output", exact: true })
    .waitFor();
  assert.equal(
    await publicationSection.getByRole("figure", { name: "Publication output chart" }).count(),
    0,
  );
  await page.screenshot({ path: `${output}/loading.png` });
  await settled();
  mode = "empty";
  await page.reload();
  for (const section of await page.locator("[data-overview-chart]").all()) {
    await section.scrollIntoViewIfNeeded();
    await section.locator("[data-overview-card]").first().waitFor();
  }
  await settled();
  await page.screenshot({ path: `${output}/empty.png`, fullPage: true });
  mode = "stale";
  await page.reload();
  await page.locator("#operations").scrollIntoViewIfNeeded();
  await page.locator("#operations [data-overview-card]").waitFor();
  assert.equal(
    await page
      .locator("[data-overview-chart]")
      .getByText(/Last updated|^Updated /)
      .count(),
    0,
    "Charts omit repeated freshness timestamps",
  );
  mode = "all-clear";
  await page.setViewportSize({ width: 1743, height: 786 });
  await page.reload();
  await page.locator("#operations").scrollIntoViewIfNeeded();
  await page.getByText("All clear", { exact: true }).waitFor();
  await page.getByRole("figure", { name: "Publication output chart" }).waitFor();
  const clearStrip = await page.locator('[data-overview-card="attention"]').boundingBox();
  assert.ok(clearStrip.height <= 80, "Empty review queues collapse to a narrow all-clear strip");
  assert.equal(await page.locator('[data-overview-card="attention"]').getByRole("link").count(), 0);
  await page.screenshot({ path: `${output}/desktop-all-clear-1743x786.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  await page.screenshot({ path: `${output}/mobile-first-390x844.png` });
  mode = "live";
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.reload();
  const workload = page.locator('[data-overview-card="queue-pressure"]');
  await page.locator("#processing").scrollIntoViewIfNeeded();
  await workload.waitFor();
  assert.equal(await workload.getByRole("status", { name: "Workload live status" }).count(), 0);
  assert.equal(await workload.getByText(/Checked \d+s ago|every \d+s|since last check/).count(), 0);
  mode = "live-changed";
  await workload
    .getByRole("table", { name: "Workload counts by job type" })
    .getByText("8,976", { exact: true })
    .waitFor({ timeout: 15000 });
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
      .locator("svg.recharts-surface")
      .count(),
    1,
    "Queue activity uses the shared Recharts donut",
  );
  await page.setViewportSize({ width: 1920, height: 600 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForFunction(() => {
    const bounds = document.querySelector("#processing").getBoundingClientRect();
    return bounds.bottom <= 0 || bounds.top >= innerHeight;
  });
  await settled();
  const workloadRequests = requests.filter(({ chart }) => chart === "queue-pressure").length;
  await page.waitForTimeout(6000);
  assert.equal(
    requests.filter(({ chart }) => chart === "queue-pressure").length,
    workloadRequests,
    "Offscreen charts stop polling",
  );
  mode = "live-failure";
  await page.locator("#processing").scrollIntoViewIfNeeded();
  await page.locator("#queue-pressure").getByRole("alert").waitFor({ timeout: 15000 });
  assert.equal(
    await page.locator('[data-overview-chart="throughput"]').getByRole("alert").count(),
    0,
    "A queue-pressure failure leaves the independently fetched throughput chart usable",
  );
  mode = "live-changed";
  await page
    .locator("#queue-pressure")
    .getByRole("alert")
    .waitFor({ state: "hidden", timeout: 15000 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  await workload.screenshot({ path: `${output}/workload-live-mobile.png` });
  // Review both application themes with the actual shared tooltip and Select surface open.
  mode = "populated";
  for (const previewTheme of ["dark", "light"]) {
    theme = previewTheme;
    await page.setViewportSize({ width: 1743, height: 786 });
    await page.goto(`${origin}/start`);
    await page.getByRole("figure", { name: "Publication output chart" }).waitFor();
    await settled();
    await page.screenshot({ path: `${output}/charts-${theme}-desktop.png` });
    const trend = page.getByRole("figure", { name: "Publication delay chart" });
    await trend.locator(".recharts-wrapper").hover({ position: { x: 180, y: 60 } });
    await trend.getByRole("status").waitFor();
    await page.screenshot({ path: `${output}/charts-${theme}-desktop-tooltip.png` });
    await page.locator("#audience").scrollIntoViewIfNeeded();
    await page.getByRole("combobox", { name: "Audience metric" }).click();
    await page.getByRole("option", { name: "Estimated readers", exact: true }).waitFor();
    await page.screenshot({ path: `${output}/charts-${theme}-desktop-select.png` });
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.screenshot({ path: `${output}/charts-${theme}-mobile.png` });
    await page.locator("#audience").scrollIntoViewIfNeeded();
    await page.getByRole("combobox", { name: "Audience metric" }).click();
    await page.getByRole("option", { name: "Estimated readers", exact: true }).waitFor();
    await page.screenshot({ path: `${output}/charts-${theme}-mobile-select.png` });
    await page.keyboard.press("Escape");
  }
  assert.deepEqual(errors, []);
  const maximumConcurrentChartRequests = maximum;
  await page.close();
  mode = "populated";
  // Separate nonce-check navigations must not affect overview request measurements.
  await checkNonceCsp(context, origin);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        maximumConcurrentChartRequests,
        initialMaximumConcurrentChartRequests,
        initialSections,
        initialCharts,
        notificationRequests: inboxRequests,
        scenarios: [
          "desktop",
          "mobile",
          "twelve independent chart requests with a four-request concurrency cap",
          "local selectors reuse their own chart data",
          "one failed chart leaves its sibling usable",
          "loading",
          "empty",
          "stale snapshots without per-panel timestamps",
          "range change",
          "independent chart failure and targeted retry",
          "delayed preparing charts recover automatically with bounded completion checks",
          "sticky sidebar at 1080px and 600px heights",
          "shared collapsed rail, persisted expansion, active routes and keyboard toggle",
          "pinned toggle after scrolling long navigation, mobile link/Escape dismissal",
          "background polling, offscreen pause and error recovery without a status strip",
          "revision-guarded CSRF recovery refreshes only attention",
          "real inbox SDK bell, items, counts, seen and read through native gateway operations",
          "native EventSource receives an unbuffered hint before the stream closes",
        ],
        pageErrors: errors,
      },
      null,
      2,
    ),
  );
  const logoutPage = await context.newPage();
  logoutPage.on("pageerror", (error) => errors.push(error.message));
  await logoutPage.setViewportSize({ width: 1440, height: 1000 });
  await logoutPage.goto(`http://127.0.0.1:${port}/start`);
  await logoutPage.route("https://identity.example/**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Provider logout</p>" }),
  );
  await logoutPage.getByRole("button", { name: /^User menu:/ }).click();
  const signOut = logoutPage.getByRole("menuitem", { name: "Sign out", exact: true });
  await signOut.waitFor();
  assert.equal(
    await logoutPage
      .getByText("Also ends your SSO session; other apps may sign you out.", { exact: true })
      .count(),
    0,
  );
  await signOut.click();
  await logoutPage.waitForURL("https://identity.example/end-session?state=admin");
  assert.deepEqual(errors, []);
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
