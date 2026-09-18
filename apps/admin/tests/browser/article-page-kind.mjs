import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. The API is a disposable local fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
let saved;
const article = {
  id: "article-1",
  title: "RSS",
  slug: "rss-25374",
  canonical_url: "https://kau.sh/rss/",
  summary: "Subscribe to this site's feeds.",
  language: "en",
  content_type: "article",
  content_format: "article",
  editorial_revision: 4,
  review_status: "pending",
  publication_status: "unpublished",
  classification_provenance: { developer_relevance: "relevant" },
  tags: [],
  topics: [],
  sources: [],
  origins: [],
};
const fixture = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  let body = {};
  if (pathname.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: 4102444800,
      csrf_token: "fixture",
    };
  else if (pathname.endsWith("/settings"))
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0 } };
  else if (pathname.endsWith("/notifications/config")) body = { enabled: false };
  else if (pathname.endsWith("/articles/article-1/classify")) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    saved = JSON.parse(Buffer.concat(chunks).toString());
    article.classification_provenance.page_kind = saved.page_kind;
    body = article;
  } else if (pathname.endsWith("/articles/article-1")) body = article;
  else body = { items: [], total: 0, limit: 50, offset: 0 };
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
  await page.goto(`${origin}/content/articles/article-1/classify`);
  const kind = page.getByRole("combobox", { name: /Page kind/ });
  await kind.waitFor();
  assert.match(await kind.innerText(), /uncertain/i);
  await kind.click();
  await page.getByRole("option", { name: /non.article/i }).click();
  await mkdir(`${root}/apps/admin/.next/browser-checks`, { recursive: true });
  await page.screenshot({ path: `${root}/apps/admin/.next/browser-checks/page-kind-desktop.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${root}/apps/admin/.next/browser-checks/page-kind-mobile.png` });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByRole("button", { name: "Save classification" }).click();
  await page.waitForURL("**/articles/article-1");
  assert.equal(saved.page_kind, "non_article");
  await page.goto(`${origin}/content/articles/article-1/classify`);
  assert.match(await kind.innerText(), /non.article/i);
  assert.deepEqual(errors, []);
  console.log(
    "Article page-kind browser checks passed: legacy default, explicit non-article decision, persistence, desktop and mobile.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}
