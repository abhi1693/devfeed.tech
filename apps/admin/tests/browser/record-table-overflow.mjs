import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. All API calls stay on this disposable fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const output = process.env.DEVFEED_BROWSER_SCREENSHOT_DIR ?? "/tmp/devfeed-record-table-overflow";
const sourceId = "11111111-1111-4111-8111-111111111111";
const articles = [
  {
    id: "22222222-2222-4222-8222-222222222222",
    title: "A practical guide to reliable software architecture and production engineering: "
      .repeat(5)
      .trim(),
  },
  { id: "33333333-3333-4333-8333-333333333333", title: "UnbrokenDeveloperTitle".repeat(22) },
].map((article) => ({
  ...article,
  canonical_url: `https://publication.example/articles/${article.id}`,
  review_status: "approved",
  publication_status: "published",
  language: "en-us",
  discovered_at: "2026-10-17T10:30:00Z",
}));
const articleRequests = [];
const pageOf = (items) => ({ items, total: items.length, limit: 25, offset: 0 });
const fixture = createServer((request, response) => {
  const url = new URL(request.url, "http://localhost");
  const path = url.pathname;
  let body;
  if (path === "/v1/admin/auth/me")
    body = {
      subject: "fixture",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (path === "/v1/admin/settings")
    body = {
      appearance: { theme: "light" },
      defaults: { refresh_seconds: 0 },
    };
  else if (path === "/v1/admin/notifications/config") body = { enabled: false };
  else if (path === "/v1/admin/ai/connection")
    body = { state: "disabled", message: "Disabled", quota: [] };
  else if (path === `/v1/admin/sources/${sourceId}`)
    body = {
      id: sourceId,
      name: "Long-title engineering feed",
      slug: "long-title-engineering-feed",
      feed_url: "https://publication.example/feed",
      website_url: "https://publication.example",
      source_type: "publisher",
      enabled: true,
      approval_status: "approved",
      poll_interval_seconds: 43200,
      publication_policy: "manual",
      publication_policy_revision: 0,
    };
  else if (path === "/v1/admin/articles") {
    articleRequests.push(url.searchParams.get("source_id"));
    body = pageOf(articles);
  } else body = pageOf([]);
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
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
app.stdout.on("data", (chunk) => {
  logs += chunk;
});
app.stderr.on("data", (chunk) => {
  logs += chunk;
});
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (app.exitCode !== null) throw new Error(logs);
    try {
      await fetch(`${origin}/login`);
      ready = true;
      break;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(ready, "The isolated production admin server started");
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 2048, height: 1100 } });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const [name, route] of [
    ["source-related", `/content/sources/${sourceId}/related`],
    ["articles", "/content/articles"],
  ]) {
    await page.goto(origin + route);
    const table = page.getByRole("table", { name: "Articles", exact: true });
    await table.getByRole("link", { name: articles[0].title, exact: true }).waitFor();
    for (const width of [2048, 1280, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
      const wrapper = table.locator("..");
      await wrapper.evaluate((node) => {
        node.scrollLeft = 0;
      });
      await page.screenshot({
        path: `${output}/${name}-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
      for (const article of articles) {
        const title = table.getByRole("link", { name: article.title, exact: true });
        const geometry = await title.evaluate((link) => {
          const cell = link.closest("td");
          const row = cell.parentElement;
          const table = cell.closest("table");
          const reviewIndex = [...table.tHead.rows[0].cells].findIndex(
            (header) => header.textContent.trim() === "Review",
          );
          const review = row.cells[reviewIndex].getBoundingClientRect();
          const bounds = cell.getBoundingClientRect();
          const padding = getComputedStyle(cell);
          const range = document.createRange();
          range.selectNodeContents(link);
          const text = [...range.getClientRects()]
            .filter((rect) => rect.width > 0)
            .map((rect) => ({ left: rect.left, right: rect.right }));
          return {
            left: bounds.left + parseFloat(padding.paddingLeft),
            right: bounds.right - parseFloat(padding.paddingRight),
            reviewLeft: review.left,
            text,
          };
        });
        assert.ok(geometry.text.length > 0, "The title has rendered text");
        for (const rect of geometry.text)
          assert.ok(
            rect.left >= geometry.left - 1 &&
              rect.right <= Math.min(geometry.right, geometry.reviewLeft) + 1,
            `${name} at ${width}px: title text crosses its own column or Review column: ${JSON.stringify(geometry)}`,
          );
        assert.ok(geometry.text.length > 1, `${name} at ${width}px: the long title wraps`);
        const row = title.locator("xpath=ancestor::tr");
        await row.getByText("Approved", { exact: true }).waitFor();
        await row.getByText("Published", { exact: true }).waitFor();
        await row.getByText("English (United States)", { exact: true }).waitFor();
        assert.equal(await row.locator("time[datetime]").count(), 1);
        for (const action of ["Edit", "Delete"]) {
          const link = row.getByRole("link", { name: action, exact: true });
          assert.equal(
            await link.getAttribute("href"),
            `/content/articles/${article.id}/${action.toLowerCase()}`,
          );
        }
      }
      const bounds = await wrapper.evaluate((node) => ({
        right: node.getBoundingClientRect().right,
        viewport: innerWidth,
        pageWidth: document.documentElement.scrollWidth,
      }));
      assert.ok(
        bounds.pageWidth <= bounds.viewport + 1,
        `${name} at ${width}px: the page does not overflow horizontally`,
      );
      assert.ok(bounds.right <= bounds.viewport + 1, "The table wrapper stays inside the page");
      if (width === 390) {
        const lastAction = table.getByRole("link", { name: "Delete", exact: true }).last();
        await lastAction.scrollIntoViewIfNeeded();
        assert.ok(
          await lastAction.evaluate((link) => {
            const control = link.getBoundingClientRect();
            const viewport = link.closest('[data-slot="table-container"]').getBoundingClientRect();
            return control.left >= viewport.left - 1 && control.right <= viewport.right + 1;
          }),
          "Mobile users can scroll the table to reach row actions",
        );
      }
    }
  }
  assert.ok(articleRequests.includes(sourceId), "Related Articles are filtered by the source");
  assert.ok(articleRequests.includes(null), "The main Articles list is exercised independently");
  assert.deepEqual(errors, []);
  console.log(
    "Article title overflow browser checks passed: source-related/main lists, spaced/unbroken titles, 2048/1280/390px text bounds, readable status/date/actions and contained mobile scrolling.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
}
