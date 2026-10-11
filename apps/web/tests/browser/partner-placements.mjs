import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { chromium } from "playwright";

// Exercise actual production reader bundles and packaged extensions, with controlled
// delivery responses. Backend integration tests prove relevance and signed receipts.
const topic = {
  id: "66666666-6666-4666-8666-666666666666",
  slug: "typescript",
  name: "TypeScript",
  kind: "language",
  description: "Typed JavaScript",
  ai_description: null,
  logo_url: null,
};
const source = {
  id: "77777777-7777-4777-8777-777777777777",
  slug: "engineering",
  name: "Engineering Journal",
  website_url: "https://publisher.test",
  logo_url: null,
};
const article = {
  id: "88888888-8888-4888-8888-888888888888",
  slug: "validate-typescript-inputs",
  title: "Validate TypeScript inputs",
  canonical_url: "https://publisher.test/typescript",
  summary: "A practical tutorial about validating TypeScript application inputs.",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: null,
  content_type: "tutorial",
  content_format: "article",
  language: "en",
  published_at: "2026-10-10T10:00:00Z",
  feed_at: "2026-10-10T10:00:00Z",
  tags: [],
  topics: [{ ...topic, role: "primary" }],
  sources: [source],
};
const ordinary = {
  product_id: "11111111-1111-4111-8111-111111111111",
  asset_id: "22222222-2222-4222-8222-222222222222",
  campaign_id: null,
  name: "TypeScript validator",
  description: "Validate application inputs.",
  url: "https://partner.example/validator",
  partner_name: "Free partner",
  sponsored: false,
  reason: "Supports the TypeScript validation task in this article.",
  receipt: "ordinary-delivery",
  frequency_cap: 1,
};
const sponsored = {
  ...ordinary,
  product_id: "33333333-3333-4333-8333-333333333333",
  asset_id: "44444444-4444-4444-8444-444444444444",
  campaign_id: "55555555-5555-4555-8555-555555555555",
  name: "Sponsored validator",
  partner_name: "Paid partner",
  sponsored: true,
  receipt: "sponsored-delivery",
  frequency_cap: 2,
};
const news = {
  ...article,
  id: "99999999-9999-4999-8999-999999999999",
  slug: "unrelated-news",
  title: "Unrelated industry news",
  content_type: "news",
};
const temp = await mkdtemp(path.join(tmpdir(), "devfeed-placement-browser-"));
await mkdir("reports/partner-placements", { recursive: true });
let signedIn = false;
let queries = [];
let events = [];

function response(requestPath, body, headers = {}, extension = false) {
  const url = new URL(requestPath, "https://devfeed.tech");
  const pathname = url.pathname.replace(/^\/api/, "");
  if (["/v1/partner-placements/query", "/v1/user/partner-placements/query"].includes(pathname)) {
    assert.equal(headers.authorization, undefined);
    if (pathname === "/v1/partner-placements/query") {
      assert.equal(headers.cookie, undefined);
      assert.equal(headers["x-csrf-token"], undefined);
    } else assert.equal(headers["x-csrf-token"], "fixture-csrf");
    queries.push({ pathname, body });
    const eligible =
      body.surface === "topic" ||
      pathname.includes("/user/") ||
      (body.surface === "article" && body.article_id === article.id);
    return {
      items: eligible
        ? [ordinary, sponsored].filter(
            (item) => !body.exclude_product_ids?.includes(item.product_id),
          )
        : [],
    };
  }
  if (pathname === "/v1/partner-tracking/events") {
    assert.equal(headers.cookie, undefined);
    assert.equal(headers.authorization, undefined);
    assert.deepEqual(Object.keys(body).sort(), ["kind", "receipt"]);
    assert.ok([ordinary.receipt, sponsored.receipt].includes(body.receipt));
    events.push(body);
    return null;
  }
  if (pathname === "/v1/user/auth/me")
    return signedIn
      ? {
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          name: "Reader",
          email: null,
          expires_at: 9999999999,
          csrf_token: "fixture-csrf",
        }
      : null;
  if (pathname === "/v1/user/auth/config") return { enabled: true, providers: [] };
  if (pathname === "/v1/user/settings/profile")
    return {
      display_name: "Reader",
      avatar_url: null,
      username: "fixture-reader",
      links: [],
      stack: [],
    };
  if (pathname === "/v1/user/settings/feed")
    return { view: "cards", content_types: ["tutorial", "news"], languages: ["en"] };
  if (pathname === "/v1/user/preferences/topics") return { topic_ids: [topic.id] };
  if (pathname === "/v1/user/preferences/sources") return { source_ids: [] };
  if (pathname === "/v1/user/engagement") return { items: [] };
  if (pathname === "/v1/user/feed")
    return {
      items: [article],
      next_cursor: null,
      status: "ready",
      has_interests: true,
      reasons: {},
      feed_kind: "personalized",
      refresh_state: "idle",
    };
  if (pathname === "/v1/feed") return { items: [article], next_cursor: null };
  if (pathname === "/v1/feed/options")
    return { sources: [source], content_types: ["tutorial", "news"] };
  if (pathname === "/v1/topics/typescript") return topic;
  if (pathname === "/v1/topics") return extension ? { items: [topic], next_offset: null } : [topic];
  if (pathname === "/v1/sources")
    return extension ? { items: [source], next_offset: null } : [source];
  if (pathname.startsWith("/v1/articles/")) {
    const selected = pathname.endsWith(news.slug) ? news : article;
    return extension ? { article: selected, topic } : selected;
  }
  return {};
}

const fixture = createServer(async (req, res) => {
  try {
    let raw = "";
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : undefined;
    const json = response(req.url, body, req.headers);
    res.writeHead(json === null ? 204 : 200, { "Content-Type": "application/json" });
    res.end(json === null ? undefined : JSON.stringify(json));
  } catch (error) {
    console.error(error);
    res.writeHead(500);
    res.end();
  }
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const upstream = `http://127.0.0.1:${fixture.address().port}`;
const env = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
);
const app = spawn(
  process.execPath,
  [
    path.resolve("node_modules/next/dist/bin/next"),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: path.resolve("apps/web"),
    env: {
      ...env,
      DEVFEED_PUBLIC_API_URL: upstream,
      DEVFEED_USER_API_URL: upstream,
      DEVFEED_USER_BASE_URL: origin,
      DEVFEED_ANALYTICS_ENABLED: "false",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (data) => {
  logs += data;
});
app.stderr.on("data", (data) => {
  logs += data;
});
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      await fetch(`${origin}/login`);
      ready = true;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  assert.ok(ready, logs);
  for (const platform of ["web", "chrome", "edge"]) {
    signedIn = false;
    queries = [];
    events = [];
    const extension = platform === "web" ? null : path.resolve(`apps/extensions/dist/${platform}`);
    const context = await chromium.launchPersistentContext(path.join(temp, platform), {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        ? undefined
        : platform === "edge"
          ? "msedge"
          : "chromium",
      headless: true,
      viewport: { width: 1280, height: 900 },
      args: extension
        ? [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
        : [],
    });
    try {
      let base = origin;
      if (extension) {
        const manifest = JSON.parse(await readFile(path.join(extension, "manifest.json"), "utf8"));
        const id = createHash("sha256")
          .update(Buffer.from(manifest.key, "base64"))
          .digest("hex")
          .slice(0, 32)
          .replace(/[0-9a-f]/g, (digit) => String.fromCharCode(97 + parseInt(digit, 16)));
        base = `chrome-extension://${id}/newtab.html#`;
        await context.route("https://devfeed.tech/api/**", async (route) => {
          const request = route.request();
          const raw = request.postData();
          const body = raw ? JSON.parse(raw) : undefined;
          const json = response(request.url(), body, request.headers(), true);
          await route.fulfill(json === null ? { status: 204 } : { json });
        });
      }
      await context.route("https://partner.example/**", (route) =>
        route.fulfill({ contentType: "text/html", body: "<p>Partner destination</p>" }),
      );
      await context.addCookies([
        {
          name: "devfeed_user_session",
          value: "private-fixture",
          url: extension ? "https://devfeed.tech" : origin,
          httpOnly: true,
        },
      ]);
      await context.addInitScript(() => localStorage.setItem("devfeed:dev-card-promo-seen", "1"));
      const page = await context.newPage();
      await page.goto(`${base}/articles/${article.slug}`);
      const free = page.getByRole("link", { name: ordinary.name, exact: true });
      await free.waitFor();
      await page.getByRole("link", { name: sponsored.name, exact: true }).waitFor();
      assert.equal(
        await page
          .locator(".partner-placement-label")
          .allTextContents()
          .then((labels) => labels.sort())
          .then(JSON.stringify),
        JSON.stringify(["Relevant product", "Sponsored"]),
      );
      assert.equal(
        await page.getByRole("link", { name: sponsored.name, exact: true }).getAttribute("rel"),
        "sponsored noopener noreferrer",
      );
      await free.scrollIntoViewIfNeeded();
      await page.waitForFunction(() =>
        JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1"))?.entries.some(
          (entry) => entry.count > 0,
        ),
      );
      const popupEvent = context.waitForEvent("page");
      await free.click();
      const popup = await popupEvent;
      await popup.close();
      await page.waitForTimeout(250);
      assert.ok(
        events.some((event) => event.kind === "impression" && event.receipt === ordinary.receipt),
        `${platform}: viewable signed impression`,
      );
      assert.ok(
        events.some((event) => event.kind === "click" && event.receipt === ordinary.receipt),
        `${platform}: independent signed click`,
      );
      await page.screenshot({ path: `reports/partner-placements/${platform}-article-desktop.png` });
      await page.getByRole("button", { name: `Hide ${sponsored.name}`, exact: true }).click();
      assert.equal(await page.getByRole("link", { name: sponsored.name, exact: true }).count(), 0);
      await page.reload();
      await page.getByRole("heading", { name: article.title, level: 1, exact: true }).waitFor();
      await page.waitForTimeout(350);
      assert.equal(
        await page.locator(".partner-placement").count(),
        0,
        `${platform}: cap and hide persist`,
      );
      const articleQuery = queries.filter((query) => query.body.surface === "article").at(-1);
      assert.ok(articleQuery.body.exclude_product_ids.includes(ordinary.product_id));
      assert.ok(articleQuery.body.exclude_product_ids.includes(sponsored.product_id));
      await page.evaluate(() =>
        localStorage.removeItem("devfeed:partner-placement-preferences:v1"),
      );
      await page.goto(`${base}/articles/${news.slug}`);
      await page.getByRole("heading", { name: news.title, level: 1, exact: true }).waitFor();
      await page.waitForTimeout(300);
      assert.equal(
        await page.locator(".partner-placement").count(),
        0,
        `${platform}: no random fallback`,
      );
      await page.goto(`${base}/topics/typescript`);
      await page.getByRole("link", { name: ordinary.name, exact: true }).waitFor();
      assert.ok(
        queries.some((query) => query.body.surface === "topic" && query.body.topic_id === topic.id),
      );
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: `reports/partner-placements/${platform}-topic-mobile.png` });
      assert.ok(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        `${platform}: mobile has no horizontal overflow`,
      );
      await page.evaluate(() =>
        localStorage.removeItem("devfeed:partner-placement-preferences:v1"),
      );
      signedIn = true;
      await page.goto(`${base}/`);
      await page.getByRole("link", { name: ordinary.name, exact: true }).waitFor();
      assert.ok(
        queries.some(
          (query) =>
            query.pathname === "/v1/user/partner-placements/query" && query.body.surface === "feed",
        ),
        `${platform}: personalized endpoint`,
      );
      await page.evaluate(() =>
        localStorage.removeItem("devfeed:partner-placement-preferences:v1"),
      );
      queries = [];
      await page.goto(`${base}/articles/${article.slug}`);
      await page
        .locator(".article-modal")
        .getByRole("link", { name: ordinary.name, exact: true })
        .waitFor();
      await page.waitForTimeout(300);
      assert.equal(
        await page.locator(".article-modal .partner-placement").count(),
        2,
        `${platform}: signed-in direct article owns visible placements`,
      );
      assert.equal(
        await page.locator(".partner-placement").count(),
        2,
        `${platform}: background feed has no competing placements`,
      );
      assert.equal(
        queries.filter((query) => query.pathname === "/v1/user/partner-placements/query").length,
        0,
        `${platform}: background feed reserves no paid inventory`,
      );
      console.log(
        `${platform}: built contextual article/topic/feed placements, signed-in direct article priority, sponsored labels, signed anonymous tracking, exclusions, hide persistence, caps and narrow layout passed`,
      );
    } finally {
      await context.close();
    }
  }
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
  await rm(temp, { recursive: true, force: true });
}
