import { catalogChoices, checkCatalogScroll } from "../../../scripts/testing/catalog-scroll.mjs";
import { createServer } from "node:https";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { checkFeedOnboarding } from "../../../scripts/testing/feed-onboarding.mjs";
import { checkTopicFollow } from "../../../scripts/testing/topic-follow.mjs";
import {
  onboardingTopics,
  onboardingSources,
} from "../../../scripts/testing/onboarding-topics.mjs";

const browser = process.env.DEVFEED_EXTENSION_BROWSER ?? "chrome";
const extension = path.resolve(import.meta.dirname, `../dist/${browser}`);
const newTab = browser === "edge" ? "edge://newtab" : "chrome://newtab";
const cookieName = "__Host-devfeed_user_session";
const user = {
  user_id: "11111111-1111-4111-8111-111111111111",
  name: "Test Reader",
  email: "reader@example.test",
  // Production sessions last 30 days, beyond the browser's ~24.8-day timer limit.
  expires_at: Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60,
  csrf_token: "c".repeat(43),
};
const article = {
  id: "22222222-2222-4222-8222-222222222222",
  slug: "signed-in-article",
  canonical_url: "https://publisher.test/article",
  title: "An article from your interests",
  summary: "A signed-in preview",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: null,
  content_type: "news",
  content_format: null,
  language: "en",
  published_at: "2026-09-14T00:00:00Z",
  feed_at: "2026-09-14T00:00:00Z",
  tags: [],
  topics: [],
  sources: [],
};

test(
  "website sign-in refreshes the extension, permits CSRF-protected actions, and signs out across tabs",
  { timeout: 60000 },
  async () => {
    const profile = await mkdtemp(path.join(tmpdir(), "devfeed-auth-test-"));
    let extensionOrigin;
    let active = false;
    let rejectLogout = true;
    let liked = false;
    let feedGeneration = 1;
    let feedRefreshing = false;
    let bookmarked = false;
    let rejectFeed = true;
    let rejectNextPage = true;
    let checkedWrites = 0;
    let authenticatedStreams = 0;
    let profileName = "Reader Profile";
    let onboarding = false;
    let catalogScroll = false;
    let onboardingSaved = false;
    let rejectOnboardingPage = true;
    let rejectTopics = true;
    let rejectTopicFollow = true;
    let savedTopicIds = ["typescript"];
    const errors = [];
    const analytics = [];
    const handle = async (route) => {
      const url = new URL(route.request().url());
      const headers = await route.request().allHeaders();
      if (url.pathname === "/api/v1/extension/analytics") {
        assert.equal(headers.cookie, undefined, "analytics must not send the account cookie");
        if (route.request().method() === "POST") {
          assert.equal(headers.origin, "chrome-extension://hliakjocndflpkmfajndigbpngfcekdm");
          analytics.push(route.request().postDataJSON());
          return route.fulfill({ status: 204 });
        }
        return route.fulfill({ json: { enabled: true } });
      }
      const authenticated = active && headers.cookie?.includes(`${cookieName}=test-session`);
      const method = route.request().method();
      const send = (json, status = 200) => route.fulfill({ status, json });
      if (url.pathname === "/api/v1/user/auth/login") {
        active = true;
        return route.fulfill({
          contentType: "text/html",
          headers: {
            "Set-Cookie": `${cookieName}=test-session; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax; Path=/`,
          },
          body: "<!doctype html><title>Signed in</title><p>Sign-in completed</p>",
        });
      }
      if (url.pathname === "/api/v1/user/auth/me") return send(authenticated ? user : null);
      if (method !== "GET") {
        assert.equal(authenticated, true, "Chrome must send the real HttpOnly session cookie");
        assert.equal(headers.origin, extensionOrigin);
        assert.equal(headers["x-csrf-token"], user.csrf_token);
        checkedWrites++;
        if (url.pathname === "/api/v1/user/preferences/topics/typescript") {
          const payload = route.request().postDataJSON();
          assert.deepEqual(Object.keys(payload), ["followed"]);
          if (payload.followed && rejectTopicFollow) {
            rejectTopicFollow = false;
            return send({}, 503);
          }
          savedTopicIds = payload.followed
            ? [...savedTopicIds, "typescript"]
            : savedTopicIds.filter((id) => id !== "typescript");
          return send({ followed: payload.followed });
        }
        if (url.pathname === "/api/v1/user/preferences") {
          const payload = route.request().postDataJSON();
          assert.deepEqual(payload, { topic_ids: ["typescript", "onboarding-0", "onboarding-1"] });
          if (rejectTopics) {
            rejectTopics = false;
            return send({}, 503);
          }
          savedTopicIds = payload.topic_ids;
          onboardingSaved = true;
          return send({ topic_ids: savedTopicIds });
        }
        if (url.pathname.endsWith("/auth/logout")) {
          if (rejectLogout) {
            rejectLogout = false;
            return send({}, 503);
          }
          active = false;
          return route.fulfill({
            status: 204,
            headers: {
              "Set-Cookie": `${cookieName}=; Max-Age=0; Secure; HttpOnly; SameSite=Lax; Path=/`,
            },
          });
        }
        if (url.pathname.endsWith("/settings/profile")) {
          profileName = route.request().postDataJSON().display_name;
          return send({ display_name: profileName, avatar_url: null });
        }
        if (url.pathname.endsWith("/like")) {
          liked = route.request().postDataJSON().liked;
        }
        if (url.pathname.endsWith("/bookmark")) {
          bookmarked = route.request().postDataJSON().bookmarked;
          return send({ article_id: article.id, bookmarked });
        }
        return send({ article_id: article.id, liked, likes: liked ? 1 : 0, opens: 0, bookmarked });
      }
      if (url.pathname === "/api/v1/feed")
        return send({
          items: [article],
          next_cursor: null,
        });
      if (url.pathname === "/api/v1/feed/options")
        return send({ content_types: ["news"], sources: [], languages: ["en"] });
      if (catalogScroll && ["/api/v1/topics", "/api/v1/sources"].includes(url.pathname)) {
        const items = catalogChoices(url.pathname.endsWith("topics") ? "topics" : "sources");
        const offset = Number(url.searchParams.get("offset") ?? 0);
        return send({
          items: items.slice(offset, offset + 60),
          next_cursor: offset + 60 < items.length ? String(offset + 60) : null,
        });
      }
      if (url.pathname === "/api/v1/sources")
        return send({ items: onboardingSources, next_cursor: null });
      if (
        url.pathname === "/api/v1/topics" &&
        onboarding &&
        url.searchParams.get("offset") === "60"
      ) {
        if (rejectOnboardingPage) {
          rejectOnboardingPage = false;
          await new Promise((resolve) => setTimeout(resolve, 1000));
          return send({}, 503);
        }
        return send({ items: [], next_cursor: null });
      }
      if (url.pathname === "/api/v1/topics")
        return send({
          items:
            onboarding || onboardingSaved
              ? onboardingTopics({
                  id: "typescript",
                  name: "TypeScript",
                  slug: "typescript",
                  kind: "language",
                  logo_url: null,
                  description: "Typed JavaScript",
                  ai_description: null,
                })
              : [],
          next_cursor: onboarding ? "60" : null,
        });
      if (url.pathname.startsWith("/api/v1/articles/")) return send({ article, topic: null });
      if (url.pathname === "/api/v1/user/engagement")
        return send([
          {
            article_id: article.id,
            liked: !!authenticated && liked,
            likes: liked ? 1 : 0,
            opens: 0,
            bookmarked: !!authenticated && bookmarked,
          },
        ]);
      if (!authenticated) return send({}, 401);
      const endpoint = url.pathname.replace("/api/v1/user/", "");
      if (endpoint === "settings/profile")
        return send({ display_name: profileName, avatar_url: null });
      if (endpoint === "settings/appearance") return send({ theme: "dark" });
      if (endpoint === "settings/feed") return send({ view: "cards", content_types: ["news"] });
      if (endpoint === "settings/notifications") return send({ show_badge: true, sound: false });
      if (endpoint === "notifications/config")
        return send({ enabled: true, environment: "users", subscriber_id: "user_a" });
      if (endpoint.endsWith("/stream")) {
        authenticatedStreams++;
        return route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" });
      }
      if (endpoint.endsWith("/counts")) return send({ unread: 0, unseen: 0 });
      if (endpoint.endsWith("/items")) return send({ items: [], next_cursor: null });
      if (endpoint === "preferences") return send({ topic_ids: savedTopicIds });
      if (endpoint.endsWith("/preferences")) return send({ preferences: [] });
      if (endpoint === "preferences/sources") return send({ source_ids: [] });
      if (endpoint === "feed") {
        if (onboarding) {
          if (onboardingSaved) onboarding = false;
          return send({
            items: [],
            next_cursor: null,
            has_interests: onboardingSaved,
            status: onboardingSaved ? "refreshing" : "ready",
            reasons: {},
          });
        }
        if (rejectFeed) {
          rejectFeed = false;
          return send({}, 503);
        }
        if (url.searchParams.has("cursor")) {
          rejectNextPage = false;
          return send({}, 409);
        }
        const selectedGeneration = Number(url.searchParams.get("generation") ?? feedGeneration);
        return send({
          items: [
            {
              ...article,
              title:
                selectedGeneration === 1
                  ? article.title
                  : selectedGeneration === 2
                    ? "Updated recommendation"
                    : "Hourly recommendation",
            },
          ],
          generation: String(selectedGeneration),
          next_cursor: rejectNextPage ? "outdated" : null,
          status: feedRefreshing ? "refreshing" : "ready",
          has_interests: true,
          reasons: {},
        });
      }
      if (endpoint === "bookmarks")
        return send({ items: bookmarked ? [article] : [], next_cursor: null });
      return send({}, 404);
    };
    execFileSync(
      "openssl",
      [
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        path.join(profile, "key.pem"),
        "-out",
        path.join(profile, "cert.pem"),
        "-days",
        "1",
        "-subj",
        "/CN=devfeed.tech",
      ],
      { stdio: "ignore" },
    );
    const server = createServer(
      {
        key: await readFile(path.join(profile, "key.pem")),
        cert: await readFile(path.join(profile, "cert.pem")),
      },
      async (request, response) => {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const body = Buffer.concat(chunks).toString();
        try {
          await handle({
            request: () => ({
              url: () => `https://devfeed.tech${request.url}`,
              allHeaders: async () => request.headers,
              method: () => request.method,
              postDataJSON: () => JSON.parse(body),
            }),
            fulfill: async ({ status = 200, json, body = "", contentType, headers = {} }) => {
              response.writeHead(status, {
                "Content-Type": contentType ?? "application/json",
                ...headers,
              });
              response.end(json === undefined ? body : JSON.stringify(json));
            },
          });
        } catch (error) {
          errors.push(error.message);
          response.writeHead(500);
          response.end();
        }
      },
    );
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const context = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        ? undefined
        : browser === "edge"
          ? "msedge"
          : "chromium",
      headless: true,
      viewport: { width: 1440, height: 1000 },
      args: [
        `--disable-extensions-except=${extension}`,
        `--load-extension=${extension}`,
        `--host-resolver-rules=MAP devfeed.tech 127.0.0.1:${server.address().port}`,
        "--no-proxy-server",
        "--ignore-certificate-errors",
      ],
    });

    try {
      const page = await context.newPage();
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(newTab);
      extensionOrigin = page.url().split("/").slice(0, 3).join("/");
      await page.waitForURL(/#\/latest$/);
      const opened = context.waitForEvent("page");
      await page.getByRole("link", { name: "Sign in", exact: true }).click();
      const login = await opened;
      await login.getByText("Sign-in completed").waitFor();
      await page.bringToFront();
      await page.waitForTimeout(150);
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.getByRole("button", { name: "User menu: Reader Profile", exact: true }).waitFor();
      assert.equal(await page.locator("html").getAttribute("class"), "dark");
      const session = (await context.cookies("https://devfeed.tech")).find(
        (cookie) => cookie.name === cookieName,
      );
      assert.ok(session.expires > Date.now() / 1000 + 29 * 86400);
      assert.ok(session.httpOnly && session.secure);
      const returningTab = await context.newPage();
      await returningTab.goto(newTab);
      await returningTab
        .getByRole("button", { name: "User menu: Reader Profile", exact: true })
        .waitFor();
      await returningTab.close();
      await page.bringToFront();

      await page.getByRole("button", { name: "Notifications", exact: true }).click();
      await page.getByText("No notifications", { exact: true }).waitFor();
      assert.equal(
        await page.locator(".chimely-header").evaluate((el) => getComputedStyle(el).display),
        "flex",
      );
      assert.equal(
        await page.locator(".chimely-list").evaluate((el) => getComputedStyle(el).listStyleType),
        "none",
      );
      await page.screenshot({
        path: path.resolve(extension, "../reader-notifications.png"),
        animations: "disabled",
      });
      await page.getByRole("link", { name: "Notification settings", exact: true }).click();
      assert.ok(page.url().endsWith("#/settings/notifications"));
      await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
      await page.goto(page.url().split("#")[0] + "#/settings/profile");
      await page.getByRole("textbox", { name: /Display name/ }).fill("Updated Reader");
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await page.getByRole("button", { name: "User menu: Updated Reader", exact: true }).waitFor();
      await page.getByRole("textbox", { name: /Display name/ }).fill("Reader Profile");
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await page.getByRole("button", { name: "User menu: Reader Profile", exact: true }).waitFor();
      for (const [label, suffix] of [
        ["Appearance", "appearance"],
        ["Feed", "feed"],
        ["Your topics", "topics"],
        ["Your sources", "sources"],
      ]) {
        await page
          .getByRole("navigation", { name: "Settings sections" })
          .getByRole("link", { name: label, exact: true })
          .click();
        await page
          .locator(`.profile-settings-nav a[href="#/settings/${suffix}"][aria-current="page"]`)
          .waitFor();
        assert.ok(page.url().endsWith(`#/settings/${suffix}`));
      }
      catalogScroll = true;
      await checkCatalogScroll(
        page,
        page.url().split("#")[0] + "#",
        path.resolve(extension, `../catalog-${browser}`),
      );
      catalogScroll = false;
      assert.ok(authenticatedStreams > 0, "notification streams carry the website session");
      rejectFeed = true;
      await page.locator(".sidebar").getByRole("link", { name: "My feed", exact: true }).click();
      await page.getByRole("heading", { name: "My feed", exact: true }).waitFor();
      assert.equal(await page.getByRole("link", { name: /^Get for (Chrome|Edge)$/ }).count(), 0);
      assert.equal(
        await page.locator(".sidebar > nav").first().getByRole("link").first().innerText(),
        "My feed",
      );
      await page.getByRole("heading", { name: "Couldn’t load your feed" }).waitFor();
      const personalUrl = page.url();
      assert.ok(personalUrl.endsWith("#/"));
      await page.getByRole("link", { name: "Try again", exact: true }).click();
      await page.locator(".article-card").first().waitFor();
      assert.equal(page.url(), personalUrl, "retry reloads the local personal feed");
      await page.locator(".pagination").scrollIntoViewIfNeeded();
      await page.getByRole("link", { name: "Show updated feed", exact: true }).click();
      await page.locator(".article-card").first().waitFor();
      assert.equal(page.url(), personalUrl, "generation recovery stays inside the extension");
      assert.equal(rejectNextPage, false, "the stale cursor was rejected");
      const feedRequests = [];
      page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/api/v1/user/feed")
          feedRequests.push(request.url());
      });
      const existingCard = await page.locator(".article-card").first().elementHandle();
      await page.getByRole("button", { name: /^Like article/ }).click();
      await page.getByRole("button", { name: /^Unlike article/ }).waitFor();
      assert.equal(feedRequests.length, 0);
      assert.equal(await existingCard.evaluate((node) => node.isConnected), true);
      assert.equal(await page.getByText("Updating recommendations in the background…").count(), 0);
      assert.equal(
        await page.getByRole("heading", { name: "Updating your feed", exact: true }).count(),
        0,
      );
      await page
        .locator(".article-card")
        .first()
        .evaluate((node) => {
          node.dataset.retained = "yes";
        });
      // Session refresh and explicit refresh must not discard the current generation.
      user.csrf_token = "d".repeat(43);
      const sessionChecked = page.waitForResponse((response) =>
        response.url().endsWith("/api/v1/user/auth/me"),
      );
      await page.waitForTimeout(150);
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await sessionChecked;
      const feedChecked = page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/api/v1/user/feed",
      );
      await page.evaluate(() => window.dispatchEvent(new Event("devfeed:extension-refresh")));
      await feedChecked;
      assert.equal(
        await page.locator(".article-card").first().getAttribute("data-retained"),
        "yes",
      );
      await page.screenshot({ path: path.resolve(extension, `../${browser}-feed-refresh.png`) });
      feedGeneration = 2;
      feedRefreshing = false;
      assert.equal(await page.getByRole("link", { name: article.title, exact: true }).count(), 1);
      feedGeneration = 3;
      const pinnedResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === "/api/v1/user/feed" && url.searchParams.get("generation") === "1";
      });
      await page.evaluate(() => window.dispatchEvent(new Event("devfeed:extension-refresh")));
      await pinnedResponse;
      await page.getByRole("link", { name: article.title, exact: true }).waitFor();
      const freshTab = await context.newPage();
      await freshTab.goto(personalUrl);
      await freshTab.getByRole("link", { name: "Hourly recommendation", exact: true }).waitFor();
      await freshTab.close();
      feedGeneration = 2;
      await page.getByRole("button", { name: "Save article for later", exact: true }).click();
      await page.getByRole("button", { name: "Remove bookmark", exact: true }).waitFor();
      await page.locator(".sidebar").getByRole("link", { name: "Read later", exact: true }).click();
      await page.locator(".card-open-link").first().click();
      await page.locator("#article-preview-title").waitFor();
      const modalUrl = page.url();
      await login.bringToFront();
      await page.bringToFront();
      await page.waitForTimeout(150);
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.locator("#article-preview-title").waitFor();
      assert.equal(page.url(), modalUrl);
      await page.reload();
      await page.locator("#article-preview-title").waitFor();
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await page.locator("dialog").waitFor({ state: "detached" });

      onboarding = true;
      savedTopicIds = [];
      await checkFeedOnboarding(
        page,
        personalUrl,
        path.resolve(extension, `../${browser}-onboarding`),
      );
      assert.ok(onboardingSaved);
      assert.deepEqual(savedTopicIds, ["typescript", "onboarding-0", "onboarding-1"]);
      const localBase = personalUrl.split("#")[0];
      await checkTopicFollow(
        page,
        `${localBase}#/topics/typescript`,
        `${localBase}#/topics/typescript/news?language=en`,
        path.resolve(extension, `../${browser}-onboarding`),
      );

      const second = await context.newPage();
      // A new tab can leave focus in the omnibox. Keep that state throughout
      // startup, and also load it behind the existing tab without interacting.
      await second.addInitScript(() => {
        Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false });
      });
      await page.bringToFront();
      await second.goto(newTab);
      await second
        .getByRole("button", { name: "User menu: Reader Profile", exact: true })
        .waitFor();
      await second.getByRole("heading", { name: "My feed", exact: true }).waitFor();
      assert.equal(new URL(second.url()).hash.replace(/^#/, "") || "/", "/");
      await second.getByRole("link", { name: "Updated recommendation", exact: true }).waitFor();
      assert.equal(await second.evaluate(() => document.hasFocus()), false);
      await second.screenshot({
        path: path.resolve(extension, `../${browser}-unfocused-feed.png`),
      });
      await page.bringToFront();
      await page.getByRole("button", { name: "User menu: Reader Profile", exact: true }).click();
      await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
      await page.getByText("Couldn’t sign out. Please try again.").waitFor();
      assert.equal(await page.locator(".user-menu-trigger").count(), 1);
      await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
      await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
      await second.getByRole("link", { name: "Sign in", exact: true }).waitFor();
      await page.waitForURL(/#\/latest$/);
      await second.waitForURL(/#\/latest$/);
      assert.equal(await page.getByRole("link", { name: "Read later", exact: true }).count(), 0);
      assert.equal(await second.getByRole("link", { name: "Read later", exact: true }).count(), 0);
      assert.ok(page.url().startsWith("chrome-extension://"));
      assert.ok(checkedWrites >= 4);
      assert.deepEqual(errors, [], "browser and fixture errors");
      assert.ok(
        analytics.some((value) => value.event.name === "article_like"),
        JSON.stringify(analytics),
      );
      assert.ok(analytics.some((value) => value.event.name === "article_bookmark"));
      assert.ok(
        analytics.some(
          (value) =>
            value.event.name === "page_view" &&
            value.event.params.page_path === "/settings/profile",
        ),
      );
      assert.equal(new Set(analytics.map((value) => value.client_id)).size, 1);
      assert.equal(new Set(analytics.map((value) => value.session_id)).size, 1);
      assert.ok(analytics.some((value) => value.engagement_time_msec > 0));
      assert.equal(JSON.stringify(analytics).includes(user.email), false);
      assert.equal(JSON.stringify(analytics).includes(user.csrf_token), false);
      const stored = await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage }),
      );
      assert.equal(stored.includes(user.csrf_token), false);
      assert.equal(stored.includes("test-session"), false);
      assert.deepEqual(errors, []);
    } finally {
      await context.close();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await rm(profile, { recursive: true, force: true });
    }
  },
);
