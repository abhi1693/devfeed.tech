import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "esbuild";
import { chromium, webkit } from "playwright";

const root = path.resolve(import.meta.dirname, "../..");
const platform = process.env.DEVFEED_HISTORY_PLATFORM ?? "web";
const engine = process.env.DEVFEED_HISTORY_BROWSER === "webkit" ? webkit : chromium;
const baseline = process.argv.includes("--baseline");
const label = baseline ? "before" : "after";
const directory = `${root}/reports/reader-history/${label}/${platform}${platform === "web" && engine === webkit ? "-webkit" : ""}`;
await mkdir(directory, { recursive: true });
const bundle = await build({
  entryPoints: [`${root}/apps/web/tests/fixtures.ts`],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const articles = Array.from({ length: 24 }, (_, i) => ({
  ...article,
  id: `22222222-2222-4222-8222-${String(i).padStart(12, "0")}`,
  slug: `history-article-${i}`,
  title: `History article ${i}`,
  image_url: null,
}));
const identity = {
  user_id: "history-user",
  name: "History Reader",
  email: null,
  csrf_token: "csrf",
  expires_at: 4102444800,
};
const profile = {
  username: "history-reader",
  display_name: "History Reader",
  avatar_url: null,
  bio: "Visibility-sensitive biography",
  links: [],
  stack: [],
  visibility: { public: true },
};
let signedIn = false;
let publicAvailable = true;
let likes = 2;
let authChecks = 0;
const fixtureResponse = (input) => {
  const url = new URL(input, "http://fixture");
  const route = url.pathname.replace(/^\/api/, "");
  let json = {};
  let status = 200;
  if (route === "/v1/user/auth/me") {
    authChecks++;
    json = signedIn ? identity : null;
  } else if (route === "/v1/user/settings/profile") json = profile;
  else if (route === "/v1/user/settings/feed")
    json = { view: "cards", languages: ["en"], content_types: ["article", "tutorial", "news"] };
  else if (route === "/v1/user/preferences/topics") json = { topic_ids: [] };
  else if (route === "/v1/user/preferences/sources") json = { source_ids: [] };
  else if (route === "/v1/user/engagement")
    json = articles
      .filter((a) => url.searchParams.getAll("article_id").includes(a.id))
      .map((a) => ({ article_id: a.id, likes, opens: 5, liked: false, bookmarked: false }));
  else if (route === "/v1/feed/options")
    json = {
      sources: [source],
      content_types: ["article", "tutorial", "news"],
      languages: ["en", "fr"],
    };
  else if (["/v1/feed", "/v1/user/bookmarks", "/v1/user/feed"].includes(route))
    json = {
      items: articles,
      next_cursor: null,
      status: "ready",
      has_interests: true,
      reasons: {},
    };
  else if (route.startsWith("/v1/articles/"))
    json = articles.find((a) => a.slug === route.split("/").at(-1)) ?? article;
  else if (route === "/v1/topics")
    json = url.pathname.startsWith("/api") ? { items: [topic], next_cursor: null } : [topic];
  else if (route === "/v1/sources")
    json = url.pathname.startsWith("/api") ? { items: [source], next_cursor: null } : [source];
  else if (route.startsWith("/v1/users/") || route.startsWith("/v1/user/profiles/")) {
    if (route.endsWith("reading-heatmap")) json = null;
    else if (!publicAvailable) {
      status = 404;
      json = { detail: "Private profile" };
    } else json = route.startsWith("/v1/users/") ? { profile, activity: null } : profile;
  }
  return {
    status,
    json,
    headers: {
      "Cache-Control": route.includes("/user") ? "private, no-store" : "public, max-age=60",
    },
  };
};
const fixture = createServer((req, res) => {
  if (req.url === "/away") {
    res.end("<h1>Elsewhere</h1>");
    return;
  }
  const { status, json, headers } = fixtureResponse(req.url);
  res.writeHead(status, { ...headers, "Content-Type": "application/json" });
  res.end(JSON.stringify(json));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const upstream = `http://127.0.0.1:${fixture.address().port}`;
const port = 18732;
const origin = `http://127.0.0.1:${port}`;
let app;
let context;
let browser;
let page;
let profileDirectory;
let logs = "";
const report = { platform, baseline, samples: [], checks: {} };
try {
  if (platform === "web") {
    const env = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
    );
    app = spawn(
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
        cwd: `${process.env.DEVFEED_HISTORY_WEB_ROOT ?? root}/apps/web`,
        env: {
          ...env,
          DEVFEED_ANALYTICS_ENABLED: "false",
          DEVFEED_X_PIXEL_ENABLED: "false",
          DEVFEED_FARO_ENABLED: "false",
          DEVFEED_PUBLIC_API_URL: upstream,
          DEVFEED_USER_API_URL: upstream,
          DEVFEED_USER_BASE_URL: origin,
        },
        stdio: "pipe",
      },
    );
    app.on("exit", (code) => {
      if (code) logs += `Server exited with ${code}\n`;
    });
    app.stdout.on("data", (d) => (logs += d));
    app.stderr.on("data", (d) => (logs += d));
    for (let i = 0; i < 100; i++) {
      if (app.exitCode !== null) throw new Error(logs);
      try {
        if (!logs.includes("Ready in")) throw new Error("Starting server");
        await fetch(`${origin}/login`);
        break;
      } catch {
        await new Promise((r) => setTimeout(r, 200));
      }
    }
    // Playwright's default disables BFCache. Remove that argument for real evidence.
    browser = await engine.launch({
      headless: true,
      ignoreDefaultArgs: ["--disable-back-forward-cache"],
    });
    context = await browser.newContext({
      viewport: { width: 1100, height: 800 },
      reducedMotion: "reduce",
    });
  } else {
    profileDirectory = await mkdtemp(path.join(tmpdir(), "devfeed-history-"));
    const extension = `${process.env.DEVFEED_HISTORY_EXTENSION_ROOT ?? root}/apps/extensions/dist/${platform}`;
    context = await chromium.launchPersistentContext(profileDirectory, {
      channel: platform === "edge" ? "msedge" : "chromium",
      headless: true,
      viewport: { width: 1100, height: 800 },
      reducedMotion: "reduce",
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    });
    await context.route("https://devfeed.tech/api/**", async (route) => {
      const response = fixtureResponse(route.request().url());
      if (/\/articles\//.test(route.request().url()) && !/\/user\//.test(route.request().url()))
        response.json = { article: response.json, topic: null };
      await route.fulfill(response);
    });
  }
  page = await context.newPage();
  await page.addInitScript(() => {
    window.__historyShows = [];
    window.addEventListener("pageshow", (event) =>
      window.__historyShows.push({ persisted: event.persisted, at: performance.now() }),
    );
  });
  let base = origin;
  if (platform !== "web") {
    await page.goto(platform === "edge" ? "edge://newtab" : "chrome://newtab");
    await page.waitForURL(/^chrome-extension:/);
    base = `${page.url().split("#")[0]}#`;
  }
  const latest = `${base}/latest?sort=oldest`;
  const waitFeed = () => page.locator(".article-card").first().waitFor();
  const nav = (href) =>
    page.locator(`.sidebar a[href="${platform === "web" ? href : `#${href}`}"]`).click();
  await page.goto(latest);
  await waitFeed();
  await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
  if (!baseline) {
    const writes = await page.evaluate(async () => {
      const replace = history.replaceState;
      let writes = 0;
      history.replaceState = function (...args) {
        writes++;
        return replace.apply(this, args);
      };
      try {
        const started = performance.now();
        let events = 0;
        while (performance.now() - started < 3000) {
          scrollTo(0, events++ % 2 ? 650 : 700);
          window.dispatchEvent(new Event("scroll"));
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        await new Promise((resolve) => setTimeout(resolve, 600));
        return writes;
      } finally {
        history.replaceState = replace;
      }
    });
    assert.ok(writes < 20, `Scroll burst must leave history capacity for navigation: ${writes}`);
    report.checks.scrollWritesBounded = true;
    report.scrollWritesDuringBurst = writes;
    await page.evaluate(() => scrollTo(0, 650));
    await page.waitForFunction(() => scrollY === 650 && history.state?.readerScroll?.[1] === 650);
    await page.reload();
    await waitFeed();
    await page.waitForFunction(() => Math.abs(scrollY - 650) < 3, undefined, { timeout: 4000 });
    report.checks.reloadScrollRestored = true;
    console.error("history: bounded scroll writes and reload restoration complete");
  }
  // Same-document history is the fast fallback when full-page BFCache is blocked.
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => scrollTo(0, 650));
    await page.waitForFunction(() => scrollY > 600);
    const expected = await page.evaluate(() => scrollY);
    await nav("/topics");
    await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
    const started = performance.now();
    await page.goBack();
    await waitFeed();
    const readyMs = Math.round(performance.now() - started);
    let scrollRestored = true;
    try {
      await page.waitForFunction((y) => Math.abs(scrollY - y) < 3, expected, { timeout: 4000 });
    } catch {
      scrollRestored = false;
    }
    report.samples.push({
      kind: "client-back",
      readyMs,
      restorationMs: Math.round(performance.now() - started),
      expectedScroll: expected,
      actualScroll: await page.evaluate(() => scrollY),
      scrollRestored,
    });
    if (!baseline)
      assert.equal(
        scrollRestored,
        true,
        "Back restores the scrolled feed after asynchronous rendering",
      );
    assert.equal(
      new URL(
        platform === "web" ? page.url() : `https://reader.test${page.url().split("#")[1]}`,
      ).searchParams.get("sort"),
      "oldest",
    );
    assert.match(
      await page.getByRole("combobox", { name: "Sort by", exact: true }).textContent(),
      /Oldest/,
    );
    await page.goForward();
    await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
    await page.goBack();
    await waitFeed();
  }
  console.error("history: scroll/filter navigation complete");
  report.checks.filtersAndForward = true;
  await page.locator(".card-open-link").nth(4).click();
  await page.locator("#article-preview-title").waitFor();
  await page.goBack();
  await page.locator("dialog.article-modal").waitFor({ state: "detached" });
  await page.goForward();
  await page.locator("#article-preview-title").waitFor();
  await page.goBack();
  await waitFeed();
  console.error("history: overlay navigation complete");
  report.checks.overlayHistory = true;
  console.error("history: checking engagement/session refresh");
  const checksBefore = authChecks;
  likes = 19;
  await nav("/topics");
  await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
  await page.goBack();
  await waitFeed();
  if (!baseline)
    await page
      .locator('.heart-button[aria-label="Sign in to like this article, 19 likes"]')
      .first()
      .waitFor({ state: "attached" });
  report.checks.historySessionRechecked = authChecks > checksBefore;
  if (!baseline) assert.equal(report.checks.historySessionRechecked, true);
  console.error("history: checking profile revocation");
  await page.goto(`${base}/users/history-reader`);
  await page.getByRole("heading", { name: "History Reader", exact: true }).waitFor();
  await nav("/topics");
  await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
  publicAvailable = false;
  await page.goBack();
  if (!baseline)
    await page.getByRole("heading", { name: "Profile unavailable", exact: true }).waitFor();
  else await page.waitForTimeout(500);
  report.checks.revokedProfileHidden =
    (await page.getByText("Visibility-sensitive biography", { exact: true }).count()) === 0;
  if (!baseline) {
    assert.equal(report.checks.revokedProfileHidden, true);
    if (platform === "web") {
      assert.match(await page.title(), /Profile unavailable/);
      const descriptions = await page
        .locator('meta[name="description"]')
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("content")));
      assert.ok(descriptions.length > 0);
      assert.ok(
        descriptions.every((value) => value === "This profile may be private or unavailable."),
      );
      const metadata = await page
        .locator("meta[content]")
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("content")));
      assert.ok(
        metadata.every(
          (value) =>
            !value.includes("History Reader") && !value.includes("Visibility-sensitive biography"),
        ),
      );
    }
  }
  publicAvailable = true;
  console.error("history: checking logout history");
  signedIn = true;
  await page.goto(`${base}/read-later`);
  if (platform !== "web") await page.reload();
  await page.getByRole("button", { name: /^User menu:/ }).waitFor();
  await waitFeed();
  await page.evaluate(() => scrollTo(0, 650));
  await nav("/topics");
  await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
  await page.goBack();
  await waitFeed();
  let personalScrollRestored = true;
  try {
    await page.waitForFunction(() => Math.abs(scrollY - 650) < 3, undefined, { timeout: 4000 });
  } catch {
    personalScrollRestored = false;
  }
  report.checks.personalScrollRestored = personalScrollRestored;
  if (!baseline) assert.equal(personalScrollRestored, true);
  await nav("/topics");
  await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
  signedIn = false;
  await page.goBack();
  if (!baseline) await page.getByRole("link", { name: "Sign in", exact: true }).first().waitFor();
  else await page.waitForTimeout(500);
  report.checks.expiredPersonalDataHidden = (await page.locator(".article-card").count()) === 0;
  if (!baseline) assert.equal(report.checks.expiredPersonalDataHidden, true);
  if (platform === "web") {
    for (let i = 0; i < 3; i++) {
      await page.goto(latest);
      await waitFeed();
      await page.evaluate(() => scrollTo(0, 650));
      await page.goto(`${upstream}/away`);
      const started = performance.now();
      const response = await page.goBack();
      await waitFeed();
      await page.waitForFunction(() => Math.abs(scrollY - 650) < 3, undefined, { timeout: 4000 });
      report.samples.push({
        kind: "document-back",
        readyMs: Math.round(performance.now() - started),
        responseCacheControl: response?.headers()["cache-control"] ?? null,
        ...(await page.evaluate(() => ({
          shows: window.__historyShows,
          navigation: performance.getEntriesByType("navigation").map((n) => ({
            type: n.type,
            notRestoredReasons: n.notRestoredReasons?.toJSON?.() ?? null,
          })),
          scroll: scrollY,
        }))),
      });
    }
  }
  await page.screenshot({ path: `${directory}/restored-reader.png` });
  if (platform === "web") {
    report.checks.optionalWebAnalyticsDisabled =
      (await page
        .locator(
          'script[src*="googletagmanager"], script[src*="clarity.ms"], script[src*="ads-twitter"]',
        )
        .count()) === 0;
    report.cookieNames = (await context.cookies()).map((cookie) => cookie.name);
    assert.equal(report.checks.optionalWebAnalyticsDisabled, true);
    assert.deepEqual(report.cookieNames, []);
  }
  report.browser = await (browser ?? context.browser()).version();
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  console.error(logs);
  if (page) {
    console.error(
      "history state",
      await page.evaluate(() => ({
        scroll: [scrollX, scrollY],
        state: history.state,
        navigation: performance.getEntriesByType("navigation").map((entry) => entry.type),
      })),
    );
    console.error(await page.locator("body").innerText());
    console.error("auth checks", authChecks);
    await page.screenshot({ path: `${directory}/failure.png` });
  }
  throw error;
} finally {
  await writeFile(`${directory}/report.json`, JSON.stringify(report, null, 2));
  await context?.close();
  await browser?.close();
  app?.kill();
  await new Promise((resolve) => fixture.close(resolve));
  if (profileDirectory) await rm(profileDirectory, { recursive: true, force: true });
}
