import { checkExtensionInstall } from "../../../../scripts/testing/extension-install.mjs";
import { searchFixture, checkSearchFilters } from "../../../../scripts/testing/search-filters.mjs";
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
let rejectTopics = true;
let onboardingSaved = false;
let rejectTopicFollow = true;
let savedTopicIds = [topic.id];
const fixture = createServer(async (req, res) => {
  const requestUrl = new URL(req.url, "http://localhost");
  const path = requestUrl.pathname;
  if (
    path === "/v1/feed" &&
    !["source_id", "topic", "tag", "q"].some((key) => requestUrl.searchParams.has(key))
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
  else if (path === "/v1/user/settings/feed")
    body = {
      view: "cards",
      content_types: ["news", "article", "tutorial", "release", "comparison", "opinion"],
    };
  else if (path === "/v1/feed/options")
    body = { sources: [source], content_types: ["article"], languages: ["en"] };
  else if (path === "/v1/topics") {
    if (mode === "onboarding")
      assert.equal(new URL(req.url, "http://localhost").searchParams.get("sort"), "articles");
    body = mode === "onboarding" ? onboardingTopics(topic) : [topic];
  } else if (path === `/v1/topics/${topic.slug}`) body = topic;
  else if (path === "/v1/sources") body = onboardingSources;
  else if (path === "/v1/user/preferences/sources") body = { source_ids: [] };
  else if (path === "/v1/user/engagement") body = [];
  else if (path === `/v1/user/preferences/topics/${topic.id}` && req.method === "PUT") {
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
      items: [article],
      next_cursor: null,
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
  await page.goto(origin);
  await page.waitForURL(`${origin}/latest`);
  await checkManagedImages(page);
  const invitation = page.getByRole("dialog", { name: "A fresh feed in every new tab." });
  await invitation.waitFor();
  assert.ok(
    (
      await invitation.getByRole("link", { name: /Install for Chrome/ }).getAttribute("href")
    ).includes("iihaipjedchahiehignbngclgpklbddo"),
  );
  assert.ok(
    (
      await invitation.getByRole("link", { name: /Install for Edge/ }).getAttribute("href")
    ).includes("fdfidbpljbdoibphcohojmlpibaepija"),
  );
  await mkdir(`${root}/reports/reader-feed`, { recursive: true });
  await page.screenshot({ path: `${root}/reports/reader-feed/extension-install-desktop.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${root}/reports/reader-feed/extension-install-mobile.png` });
  assert.ok(
    await invitation.evaluate(
      (element) => element.getBoundingClientRect().right <= window.innerWidth,
    ),
  );
  await page.keyboard.press("Escape");
  assert.equal(await invitation.count(), 0);
  await page.reload();
  await page.getByRole("heading", { name: "Latest feed", exact: true }).waitFor();
  assert.equal(await invitation.count(), 0);
  await page.setViewportSize({ width: 1440, height: 1000 });
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
    `/api/v1/user/auth/login?return_to=${encodeURIComponent(topicPath)}`,
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
  assert.equal(await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(), 1);
  assert.equal(await page.evaluate(() => document.hasFocus()), false);
  mode = "new";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    1,
  );
  const freshTab = await context.newPage();
  await freshTab.goto(origin);
  await freshTab.getByRole("link", { name: "New recommendation", exact: true }).waitFor();
  await freshTab.close();
  mode = "refreshing";
  await page.evaluate(() => window.dispatchEvent(new Event("devfeed:interests-changed")));
  await page.getByText("Updating recommendations in the background…").waitFor();
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    1,
  );
  assert.equal(await page.getByRole("heading", { name: "Updating your feed" }).count(), 0);
  const output = `${root}/reports/reader-feed`;
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: `${output}/background-refresh.png`, fullPage: true });
  mode = "new";
  await page
    .getByRole("link", { name: "New recommendation", exact: true })
    .waitFor({ timeout: 15000 });
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    0,
  );
  await page.locator(".sidebar").getByRole("link", { name: "Latest feed", exact: true }).click();
  await page.waitForURL(`${origin}/latest`);
  mode = "onboarding";
  savedTopicIds = [];
  await checkFeedOnboarding(page, origin, `${output}/web`);
  assert.ok(onboardingSaved);
  assert.deepEqual(savedTopicIds, [topic.id, "onboarding-0", "onboarding-1"]);
  await checkTopicFollow(
    page,
    `${origin}/topics/${topic.slug}`,
    `${origin}${topicPath}`,
    `${output}/web`,
  );
  await checkSearchFilters(page, `${origin}/search?q=microservice`);
  const edgeContext = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    viewport: { width: 1440, height: 1000 },
  });
  await edgeContext.addInitScript(() =>
    localStorage.setItem("devfeed:extension-install-seen", "1"),
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
