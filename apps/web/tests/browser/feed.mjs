import { checkPreviewBackground } from "../../../../scripts/testing/preview-background.mjs";
import {
  checkLanguagePreferences,
  checkFeedSort,
} from "../../../../scripts/testing/feed-preferences.mjs";
import { catalogChoices, checkCatalogScroll } from "../../../../scripts/testing/catalog-scroll.mjs";
import { checkExtensionInstall } from "../../../../scripts/testing/extension-install.mjs";
import {
  searchFixture,
  checkSearchFilters,
  checkSearchInfiniteScroll,
} from "../../../../scripts/testing/search-filters.mjs";
import {
  withManagedImage,
  mockManagedImages,
  checkManagedImages,
} from "../../../../scripts/testing/managed-images.mjs";
import { signInResponse, checkGuestTopicSignIn } from "../../../../scripts/testing/sign-in.mjs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import { checkFeedOnboarding } from "../../../../scripts/testing/feed-onboarding.mjs";
import { checkTopicFollow } from "../../../../scripts/testing/topic-follow.mjs";
import {
  onboardingTopics,
  onboardingSources,
} from "../../../../scripts/testing/onboarding-topics.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const fixtureBundle = await build({
  entryPoints: [`${root}/apps/web/tests/fixtures.ts`],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(fixtureBundle.outputFiles[0].text).toString("base64")}`
);
withManagedImage(article);
let mode = "ready";
let feedSettings = {
  view: "cards",
  content_types: ["news", "article", "tutorial", "release", "comparison", "opinion"],
  languages: ["en"],
};
let failArticle = true;
let personalFeedRequests = 0;
let rejectTopics = true;
let onboardingSaved = false;
let rejectOnboardingPage = true;
let rejectTopicFollow = true;
let savedTopicIds = [topic.id];
const fixture = createServer(async (req, res) => {
  const requestUrl = new URL(req.url, "http://localhost");
  const path = requestUrl.pathname;
  if (path === `/v1/articles/${article.slug}`) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(article));
    return;
  }
  if (path === "/v1/articles/retry-article") {
    res.writeHead(failArticle ? 503 : 200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(failArticle ? {} : { ...article, slug: "retry-article" }));
    return;
  }
  if (["/v1/feed", "/v1/topics", "/v1/sources"].includes(path))
    assert.equal(req.headers["cache-control"], "max-age=600");
  if (
    path === "/v1/feed" &&
    !["source_id", "topic", "tag", "q", "sort"].some((key) => requestUrl.searchParams.has(key))
  )
    assert.equal(requestUrl.searchParams.get("diverse"), "true");
  if (path === "/authorize") {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end("<p>Sign-in provider</p>");
    return;
  }
  if (path === "/v1/user/auth/login") {
    const result = await signInResponse(
      req.url,
      `http://127.0.0.1:${fixture.address().port}/authorize`,
    );
    res.writeHead(result.status, result.headers);
    res.end(result.body);
    return;
  }
  const authenticated = req.headers.cookie?.includes("devfeed_user_session=valid");
  if (path === "/v1/search") {
    if (requestUrl.searchParams.has("sort"))
      await new Promise((resolve) => setTimeout(resolve, 800));
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify(
        searchFixture(requestUrl.searchParams.get("q"), requestUrl.searchParams.get("sort")),
      ),
    );
    return;
  }
  if (mode === "catalog-scroll" && ["/v1/topics", "/v1/sources"].includes(path)) {
    const items = catalogChoices(path.endsWith("topics") ? "topics" : "sources").filter((item) =>
      item.name.toLowerCase().includes((requestUrl.searchParams.get("q") ?? "").toLowerCase()),
    );
    const offset = Number(requestUrl.searchParams.get("offset") ?? 0);
    const limit = Number(requestUrl.searchParams.get("limit") ?? 60);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(items.slice(offset, offset + limit)));
    return;
  }
  let body = {};
  if (path === "/v1/user/auth/me")
    body = authenticated
      ? {
          user_id: "user",
          name: "Reader",
          email: "reader@example.test",
          csrf_token: "test",
          expires_at: Date.now() / 1000 + 3600,
        }
      : null;
  else if (path === "/v1/user/auth/config") body = { enabled: true };
  else if (path === "/v1/user/settings/profile")
    body = { display_name: "Reader", avatar_url: null };
  else if (path === "/v1/user/settings/appearance") body = { theme: "light" };
  else if (path === "/v1/user/settings/feed") {
    if (req.method === "PUT") {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      feedSettings = JSON.parse(Buffer.concat(chunks).toString());
    }
    body = feedSettings;
  } else if (path === "/v1/feed/options")
    body = { sources: [source], content_types: ["article"], languages: ["en"] };
  else if (path === "/v1/topics") {
    if (mode === "onboarding")
      assert.equal(new URL(req.url, "http://localhost").searchParams.get("sort"), "articles");
    if (mode === "onboarding" && requestUrl.searchParams.get("q")) {
      body = onboardingTopics(topic).filter((item) =>
        item.name.toLowerCase().includes(requestUrl.searchParams.get("q").toLowerCase()),
      );
    } else if (mode === "onboarding" && requestUrl.searchParams.get("offset") === "60") {
      if (rejectOnboardingPage) {
        rejectOnboardingPage = false;
        await new Promise((resolve) => setTimeout(resolve, 1000));
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ detail: "Temporary catalog failure" }));
        return;
      }
      body = [];
    } else {
      const choices = onboardingTopics(topic);
      body =
        mode === "onboarding"
          ? [
              ...choices,
              ...Array.from({ length: 60 - choices.length }, (_, i) => ({
                ...topic,
                id: `extra-${i}`,
                slug: `extra-${i}`,
                name: `Extra topic ${i}`,
              })),
            ]
          : [topic];
    }
  } else if (path === `/v1/topics/${topic.slug}`) body = topic;
  else if (path === "/v1/sources") body = onboardingSources;
  else if (path === "/v1/user/preferences/sources") body = { source_ids: [] };
  else if (path === "/v1/user/engagement")
    body = [{ article_id: article.id, likes: 0, liked: false, opens: 0 }];
  else if (path === `/v1/user/articles/${article.id}/like`) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const { liked } = JSON.parse(Buffer.concat(chunks).toString());
    body = { article_id: article.id, liked, likes: liked ? 1 : 0, opens: 0 };
  } else if (path === `/v1/user/preferences/topics/${topic.id}` && req.method === "PUT") {
    assert.ok(authenticated);
    assert.equal(req.headers["x-csrf-token"], "test");
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const payload = JSON.parse(Buffer.concat(chunks).toString());
    assert.deepEqual(Object.keys(payload), ["followed"]);
    if (payload.followed && rejectTopicFollow) {
      rejectTopicFollow = false;
      res.writeHead(503, { "Content-Type": "application/json" });
      res.end("{}");
      return;
    }
    savedTopicIds = payload.followed
      ? [...savedTopicIds, topic.id]
      : savedTopicIds.filter((id) => id !== topic.id);
    body = { followed: payload.followed };
  } else if (path === "/v1/user/preferences") {
    if (req.method === "PUT") {
      assert.ok(authenticated);
      assert.equal(req.headers["x-csrf-token"], "test");
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString());
      assert.deepEqual(payload, { topic_ids: [topic.id, "onboarding-0", "onboarding-1"] });
      if (rejectTopics) {
        rejectTopics = false;
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end("{}");
        return;
      }
      savedTopicIds = payload.topic_ids;
      onboardingSaved = true;
      mode = "onboarding-refreshing";
    }
    body = { topic_ids: savedTopicIds };
  } else if (path === "/v1/user/source-preferences") body = { source_ids: [] };
  else if (path === "/v1/user/feed") {
    body = mode.startsWith("onboarding")
      ? {
          status: mode === "onboarding-refreshing" ? "refreshing" : "ready",
          has_interests: onboardingSaved,
          items: [],
          next_cursor: null,
          reasons: {},
        }
      : {
          status: mode === "refreshing" ? "refreshing" : "ready",
          generation: requestUrl.searchParams.get("generation") ?? (mode === "new" ? "new" : "old"),
          has_interests: true,
          items: [
            {
              ...article,
              title:
                (requestUrl.searchParams.get("generation") ?? mode) === "new"
                  ? "New recommendation"
                  : "Previous recommendation",
            },
          ],
          next_cursor: null,
          reasons: {},
        };
    if (mode === "onboarding-refreshing") mode = "ready";
  } else if (path === "/v1/feed")
    body = {
      items:
        mode === "scroll"
          ? requestUrl.searchParams.has("cursor")
            ? [
                {
                  ...article,
                  id: "next",
                  slug: "next",
                  title: "Automatically appended feed article",
                },
              ]
            : Array.from({ length: 24 }, (_, index) => ({
                ...article,
                id: String(index),
                slug: `scroll-${index}`,
              }))
          : [article],
      next_cursor: mode === "scroll" && !requestUrl.searchParams.has("cursor") ? "next+page" : null,
    };
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
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
    `${root}/node_modules/next/dist/bin/next`,
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: `${root}/apps/web`,
    env: {
      ...env,
      DEVFEED_PUBLIC_API_URL: upstream,
      DEVFEED_USER_API_URL: upstream,
      DEVFEED_USER_BASE_URL: origin,
    },
    stdio: "pipe",
  },
);
let logs = "",
  browser;
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
try {
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(`${origin}/login`, { redirect: "manual" });
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
  await mockManagedImages(context);
  const page = await context.newPage();
  const campaign = "utm_source=linkedin&utm_medium=organic&utm_campaign=reader_updates";
  await page.goto(`${origin}/?${campaign}&unrelated=discard`);
  await page.waitForURL(`${origin}/latest?${campaign}`);
  await checkManagedImages(page);
  const onboarding = page.getByRole("dialog", {
    name: "DevFeed is your daily briefing on what’s next.",
  });
  await onboarding.waitFor();
  assert.equal(await onboarding.getByRole("link", { name: /Install/i }).count(), 0);
  assert.ok(await onboarding.getByText(/developer news, launches, tutorials/).count());
  await mkdir(`${root}/reports/reader-feed`, { recursive: true });
  await page.screenshot({ path: `${root}/reports/reader-feed/onboarding-desktop.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${root}/reports/reader-feed/onboarding-mobile.png` });
  assert.ok(
    await onboarding.evaluate(
      (element) => element.getBoundingClientRect().right <= window.innerWidth,
    ),
  );
  await page.keyboard.press("Escape");
  assert.equal(await onboarding.count(), 0);
  await page.reload();
  await page.getByRole("heading", { name: "Latest feed", exact: true }).waitFor();
  assert.equal(await onboarding.count(), 0);
  assert.equal(await page.locator(".mobile-nav").getByRole("link", { name: "Legal" }).count(), 0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const whatsNew = page
    .locator(".sidebar")
    .getByRole("link", { name: "What’s new (opens in a new tab)", exact: true });
  assert.equal(await whatsNew.getAttribute("href"), "https://changelog.devfeed.tech/");
  assert.equal(await whatsNew.getAttribute("target"), "_blank");
  await checkExtensionInstall(
    page,
    "chrome",
    ".feed-toolbar-actions",
    `${root}/reports/reader-feed/install-button-chrome.png`,
  );

  assert.equal(await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(), 0);
  assert.equal(
    await page.locator(".mobile-nav").getByRole("link", { name: "Read later" }).count(),
    0,
  );
  await context.addCookies([{ name: "devfeed_user_session", value: "expired", url: origin }]);
  await page.goto(origin);
  await page.waitForURL(`${origin}/latest`);
  const topicPath = `/topics/${topic.slug}/articles?language=en`;
  await page.goto(`${origin}${topicPath}`);
  const guestFollow = page
    .getByRole("region", { name: "Feed controls" })
    .getByRole("link", { name: "Follow", exact: true });
  await guestFollow.waitFor();
  assert.equal(
    await guestFollow.getAttribute("href"),
    `/api/v1/user/auth/login?return_to=${encodeURIComponent(topicPath.split("?")[0])}`,
  );
  await checkGuestTopicSignIn(
    page,
    `${origin}/topics/${topic.slug}`,
    false,
    `${upstream}/authorize`,
  );
  await context.addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
  await page.addInitScript(() => {
    Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false });
  });
  await page.goto(origin);
  await page.getByRole("heading", { name: "My feed", exact: true }).waitFor();
  await checkExtensionInstall(
    page,
    "chrome",
    ".personal-feed-settings",
    `${root}/reports/reader-feed/install-button-personal.png`,
  );
  assert.equal(
    await page.locator(".sidebar > nav").first().getByRole("link").first().innerText(),
    "My feed",
  );
  await page.getByRole("link", { name: "Previous recommendation", exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/");
  assert.equal(await page.locator(".article-grid").count(), 1);
  assert.equal(await page.locator(".article-card").count(), 1);
  assert.equal(await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(), 1);
  assert.equal(await page.evaluate(() => document.hasFocus()), false);
  mode = "new";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    1,
  );
  await checkPreviewBackground(page, `${root}/reports/reader-feed/personal-preview.png`);
  const requestsBeforeLike = personalFeedRequests;
  const existingCard = await page.locator(".article-card").first().elementHandle();
  await page.getByRole("button", { name: /^Like article/ }).click();
  await page.getByRole("button", { name: /^Unlike article/ }).waitFor();
  await page.getByRole("button", { name: /^Unlike article/ }).click();
  await page.getByRole("button", { name: /^Like article/ }).waitFor();
  assert.equal(personalFeedRequests, requestsBeforeLike);
  assert.equal(await existingCard.evaluate((node) => node.isConnected), true);
  const freshTab = await context.newPage();
  await freshTab.goto(origin);
  await freshTab.getByRole("link", { name: "New recommendation", exact: true }).waitFor();
  await freshTab.close();
  mode = "refreshing";
  await page.evaluate(() => window.dispatchEvent(new Event("devfeed:interests-changed")));
  assert.equal(await page.getByText("Updating recommendations in the background…").count(), 0);
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    1,
  );
  assert.equal(await page.getByRole("heading", { name: "Updating your feed" }).count(), 0);
  const output = `${root}/reports/reader-feed`;
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: `${output}/background-refresh.png`, fullPage: true });
  await checkLanguagePreferences(page, origin);
  await checkFeedSort(page, origin);
  await checkFeedSort(page, origin, true);
  mode = "new";
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    1,
  );

  await page.locator(".sidebar").getByRole("link", { name: "Latest feed", exact: true }).click();
  await page.waitForURL(`${origin}/latest`);
  mode = "onboarding";
  savedTopicIds = [];
  // The earlier background-refresh page deliberately overrides hasFocus on every navigation.
  const onboardingPage = await context.newPage();
  await checkFeedOnboarding(onboardingPage, origin, `${output}/web`);
  await onboardingPage.close();
  await page.bringToFront();
  assert.ok(onboardingSaved);
  assert.deepEqual(savedTopicIds, [topic.id, "onboarding-0", "onboarding-1"]);
  await checkTopicFollow(
    page,
    `${origin}/topics/${topic.slug}`,
    `${origin}${topicPath}`,
    `${output}/web`,
  );
  const scrollPage = await context.newPage();
  mode = "scroll";
  await scrollPage.goto(`${origin}/latest`);
  await scrollPage.locator(".article-card").first().waitFor();
  assert.equal(await scrollPage.locator(".discovery-strip").count(), 0);
  // Streamed cards can precede the final hydrated layout; assert settled geometry.
  await scrollPage.waitForFunction(
    () => {
      const card = document.querySelector(".article-card");
      const date = card?.querySelector(".card-date");
      const actions = card?.querySelector(".article-quick-actions");
      if (!date || !actions) return false;
      const dateBox = date.getBoundingClientRect();
      const actionsBox = actions.getBoundingClientRect();
      const dateCenter = dateBox.top + dateBox.height / 2;
      const actionsCenter = actionsBox.top + actionsBox.height / 2;
      return Math.abs(dateCenter - actionsCenter) < 1 && actionsBox.left > dateBox.right;
    },
    undefined,
    { timeout: 10000 },
  );
  assert.equal(
    await scrollPage.getByRole("link", { name: "More articles", exact: true }).count(),
    0,
  );
  assert.equal(
    await scrollPage.getByRole("button", { name: "More articles", exact: true }).count(),
    0,
  );
  await scrollPage.getByRole("button", { name: /^User menu:/ }).waitFor();
  await scrollPage.locator(".pagination").scrollIntoViewIfNeeded();
  await scrollPage.getByRole("heading", { name: "Automatically appended feed article" }).waitFor();
  assert.equal(await scrollPage.locator(".article-card").count(), 25);
  assert.equal(await scrollPage.locator(".article-grid").count(), 1);
  mode = "ready";
  await checkSearchInfiniteScroll(scrollPage, `${origin}/search?q=infinite-scroll`);
  mode = "catalog-scroll";
  await checkCatalogScroll(scrollPage, origin, `${root}/reports/reader-feed/catalog-web`);
  mode = "ready";
  await scrollPage.close();
  await checkSearchFilters(page, `${origin}/search?q=microservice`);
  const edgeContext = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    viewport: { width: 1440, height: 1000 },
  });
  await edgeContext.addInitScript(() =>
    localStorage.setItem("devfeed:first-visit-onboarding-seen", "1"),
  );
  await mockManagedImages(edgeContext);
  const edgePage = await edgeContext.newPage();
  await edgePage.goto(`${origin}/latest`);
  await checkExtensionInstall(
    edgePage,
    "edge",
    ".feed-toolbar-actions",
    `${output}/install-button-edge.png`,
  );
  await edgePage.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await edgePage.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    false,
  );
  await edgePage.screenshot({ path: `${output}/install-button-narrow.png`, fullPage: true });
  await edgeContext.addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
  await edgePage.goto(origin);
  await checkExtensionInstall(
    edgePage,
    "edge",
    ".personal-feed-settings",
    `${output}/install-button-edge-personal.png`,
  );
  await edgeContext.close();
  const retryContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await retryContext.addInitScript(() =>
    localStorage.setItem("devfeed:first-visit-onboarding-seen", "1"),
  );
  const retryPage = await retryContext.newPage();
  const retryResponse = await retryPage.goto(`${origin}/articles/retry-article`);
  assert.equal(retryResponse.status(), 200);
  await retryPage.getByRole("heading", { name: "Couldn’t load the article" }).waitFor();
  assert.ok(
    (await retryPage.locator('meta[name="robots"]').getAttribute("content")).includes("noindex"),
  );
  await retryPage.screenshot({ path: `${root}/reports/article-retry-web.png` });
  failArticle = false;
  await retryPage.getByRole("button", { name: "Try again", exact: true }).click();
  await retryPage.locator("#article-preview-title").waitFor();
  await retryContext.close();
  console.log(
    "Reader routes, signed-out navigation, and background recommendation refresh passed.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.close();
}
