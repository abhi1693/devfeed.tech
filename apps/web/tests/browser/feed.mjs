import {
  youtubeReaderFixture,
  checkYoutubeReader,
} from "../../../../scripts/testing/youtube-reader.mjs";
import { checkCatalogSharing } from "../../../../scripts/testing/catalog-sharing.mjs";
import {
  checkKeyboardReader,
  keyboardProfile,
  keyboardActivity,
} from "../../../../scripts/testing/keyboard-reader.mjs";
import { checkRouteStyles } from "../../../../scripts/testing/route-styles.mjs";
import {
  checkGuestDocument,
  renderReadFixture,
  checkRenderReads,
} from "../../../../scripts/testing/document-latency.mjs";
import {
  withManagedSourceLogo,
  mockSourceLogos,
  checkSourceLogos,
  checkSourceLogoPages,
} from "../../../../scripts/testing/source-logos.mjs";

import {
  articleTopicContextFixture,
  checkArticleTopicContext,
  checkArticleTopicDocument,
} from "../../../../scripts/testing/article-topic-context.mjs";

import { checkPreviewCover } from "../../../../scripts/testing/preview-cover.mjs";
import { checkNonceCsp } from "../../../../scripts/testing/nonce-csp.mjs";
import { checkArticleGrid } from "../../../../scripts/testing/article-grid.mjs";
import { checkDefaultAll, defaultAllFeed } from "../../../../scripts/testing/default-all.mjs";
import { checkArticleViews, compactListArticle, compactLoadingGate } from "./article-views.mjs";
import { checkCompactLoading } from "../../../../scripts/testing/compact-loading.mjs";
import { checkSourceFilter, longFilterSource } from "../../../../scripts/testing/select-menus.mjs";
import { checkAccessibility } from "./accessibility.mjs";
import { dailyFixture, checkMustReads } from "../../../../scripts/testing/must-reads.mjs";
import { checkLeaderboard, leaderboardProfile } from "./leaderboard.mjs";
import { checkMcp, testMcpEndpoint } from "../../../../scripts/testing/mcp.mjs";
import {
  checkReadingStreak,
  readingStreakFixture,
} from "../../../../scripts/testing/reading-streak.mjs";
import { checkReaderNavigation } from "../../../../scripts/testing/reader-navigation.mjs";
import { checkReaderInteractions, notificationFixture } from "./reader-interactions.mjs";
import { checkSidebarGitHub } from "../../../../scripts/testing/sidebar-github.mjs";
import { checkCollapsibleSidebar } from "../../../../scripts/testing/collapsible-sidebar.mjs";
import { checkBrowserTheme } from "../../../../scripts/testing/browser-theme.mjs";
import {
  checkUserMutations,
  userMutationFixture,
} from "../../../../scripts/testing/user-mutations.mjs";
import {
  checkReaderRoutes,
  readerRoutesFixture,
} from "../../../../scripts/testing/reader-routes.mjs";
import { checkFeedMode, feedModeFixture } from "../../../../scripts/testing/feed-mode.mjs";
import {
  checkReaderContext,
  readerContextFixture,
} from "../../../../scripts/testing/reader-context.mjs";
import {
  checkReaderDestinations,
  readerDestinationsFixture,
} from "../../../../scripts/testing/reader-destinations.mjs";
import {
  engagementFeed,
  engagementRows,
  checkEngagementPagination,
} from "./engagement-pagination.mjs";
import {
  checkDevCardPromo,
  checkUnclaimedDevCardPromo,
  promoPublicProfile,
} from "../../../../scripts/testing/dev-card-promo.mjs";
import { checkPreviewBackground } from "../../../../scripts/testing/preview-background.mjs";
import { blockedFeed, checkArticleFirst, checkArticleBeforeHydration } from "./article-first.mjs";
import { checkFeedPreparation } from "../../../../scripts/testing/feed-preparation.mjs";
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
  checkSearchSecurity,
  searchSecurityQuery,
  searchSecurityEvent,
} from "../../../../scripts/testing/search-filters.mjs";
import {
  withManagedImage,
  mockManagedImages,
  checkManagedImages,
} from "../../../../scripts/testing/managed-images.mjs";
import { signInResponse, checkGuestTopicSignIn } from "../../../../scripts/testing/sign-in.mjs";
import {
  checkLoginDestinations,
  loginDestinationsFixture,
} from "../../../../scripts/testing/login-destinations.mjs";
import {
  checkServerReads,
  checkSitemapPolicy,
  checkLegalMetadata,
  serverReadFixture,
} from "../../../../scripts/testing/server-reads.mjs";
import assert from "node:assert/strict";
import {
  thumbnailProfiles,
  checkThumbnailSizing,
} from "../../../../scripts/testing/thumbnail-sizing.mjs";

import { JSDOM } from "jsdom";
import {
  snippetArticle,
  checkSnippetDocument,
  checkArticleSnippets,
} from "../../../../scripts/testing/article-snippets.mjs";
import test from "node:test";
import { createServer, request as httpRequest } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import { checkFeedOnboarding } from "./feed-onboarding.mjs";
import { checkSignupPrompts, signupPromptArticle } from "./signup-prompts.mjs";
import { checkTopicFollow } from "../../../../scripts/testing/topic-follow.mjs";
import { checkSourceFollow } from "./source-follow.mjs";
import {
  onboardingTopics,
  onboardingSources,
} from "../../../../scripts/testing/onboarding-topics.mjs";

import {
  readerDomCatalogFixture,
  checkReaderDom,
} from "../../../../scripts/testing/reader-dom.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
async function createReaderFixture(t, { thumbnailFeed = false } = {}) {
  let app, browser, fixture, userFixture;
  let logs = "";
  t.after(async () => {
    try {
      await browser?.close();
    } finally {
      if (app && app.exitCode === null && app.signalCode === null) {
        const stopped = new Promise((resolve) => app.once("exit", resolve));
        const deadline = setTimeout(() => app.kill("SIGKILL"), 5000);
        app.kill("SIGTERM");
        await stopped;
        clearTimeout(deadline);
      }
      for (const server of [fixture, userFixture]) {
        if (!server?.listening) continue;
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
      }
    }
  });
  const fixtureBundle = await build({
    entryPoints: [`${root}/apps/web/tests/fixtures.ts`],
    bundle: true,
    write: false,
    format: "esm",
  });
  const fixtures = await import(
    `data:text/javascript;base64,${Buffer.from(fixtureBundle.outputFiles[0].text).toString("base64")}`
  );
  const { article, topic, source } = structuredClone({ ...fixtures });
  topic.id = "55555555-5555-4555-8555-555555555555";
  withManagedImage(article);
  withManagedSourceLogo(article.sources[0]);
  const mustReadsFixture = dailyFixture(article);
  const readingFixture = readingStreakFixture();
  const youtube = youtubeReaderFixture(article);
  const mutationFixture = userMutationFixture({ articles: [article], topic });
  const domCatalog = readerDomCatalogFixture();
  const routeFixture = readerRoutesFixture(article);
  const feedMode = feedModeFixture(article);
  const readerContext = readerContextFixture(article);
  const readerDestinations = readerDestinationsFixture(article);
  const loginDestinations = loginDestinationsFixture(article);
  const serverReads = serverReadFixture();
  const renderReads = renderReadFixture(article, topic);
  const topicContext = articleTopicContextFixture(article);
  let mode = "ready";
  let notificationCount = 1;
  const defaultAllRequests = [];
  let compactArticles = false;
  let articleFeedGate;
  let feedSettings = {
    view: "cards",
    content_types: ["news", "article", "tutorial", "release", "comparison", "opinion"],
    languages: ["en"],
  };
  let failArticle = true;
  let bookmarked = false;
  const bookmarkedArticleIds = new Set();
  let personalFeedRequests = 0;
  let rejectTopics = true;
  let onboardingSaved = false;
  let rejectOnboardingPage = true;
  let rejectTopicFollow = true;
  let savedTopicIds = [topic.id];
  let savedSourceIds = [];
  const searchClicks = [];
  let rejectSourceFollow = true;
  fixture = createServer(async (req, res) => {
    const requestUrl = new URL(req.url, "http://localhost");
    const path = requestUrl.pathname;
    if (youtube.handles(requestUrl)) {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const { json } = youtube.respond(
        requestUrl,
        req.method,
        chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined,
      );
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (
      path === "/v1/user/profiles/keyboard-reader" ||
      path === "/v1/user/profiles/keyboard-reader/reading-heatmap"
    ) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify(path.endsWith("reading-heatmap") ? keyboardActivity : keyboardProfile),
      );
      return;
    }
    serverReads.record(req.url, req.headers, `http://${req.headers.host}`);
    if (topicContext.handles(requestUrl)) {
      const { json } = topicContext.respond(requestUrl);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (renderReads.handles(requestUrl)) {
      const { status, json } = renderReads.respond(requestUrl, req.headers);
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (serverReads.handles(requestUrl)) {
      const { status, json } = serverReads.respond(requestUrl);
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (loginDestinations.handles(requestUrl)) {
      const { status, json } = loginDestinations.respond(requestUrl);
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (readerDestinations.handles(requestUrl)) {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const response = await readerDestinations.respond(
        requestUrl,
        chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined,
      );
      res.writeHead(response.status, {
        "Content-Type": response.contentType ?? "application/json",
      });
      res.end(response.body ?? JSON.stringify(response.json));
      return;
    }
    if (readerContext.handles(requestUrl)) {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const { status, json } = await readerContext.respond(
        requestUrl,
        chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined,
      );
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (feedMode.handles(requestUrl)) {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const { status, json } = feedMode.respond(
        requestUrl,
        chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined,
      );
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (domCatalog.handles(requestUrl)) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(domCatalog.respond(requestUrl)));
      return;
    }
    routeFixture.record(requestUrl);
    if (routeFixture.handles(requestUrl)) {
      const { status, json } = routeFixture.respond(requestUrl);
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    if (mutationFixture.handles(path, req.method)) {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      if (req.method === "PUT") assert.equal(req.headers["x-csrf-token"], "test");
      const { status, json } = mutationFixture.respond(
        path,
        req.method,
        chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined,
      );
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(json));
      return;
    }
    // Collection indexing fixtures: metadata and page content use the same feed.
    const collection = requestUrl.searchParams.get("tag") || requestUrl.searchParams.get("topic");
    if (/^\/v1\/(?:tags|topics)\/seo-/.test(path)) {
      const slug = path.split("/").at(-1);
      res.writeHead(slug === "seo-missing" ? 404 : 200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify(slug === "seo-missing" ? { detail: "Not found" } : { ...topic, slug }),
      );
      return;
    }
    if (path === "/v1/feed" && collection?.startsWith("seo-")) {
      assert.equal(requestUrl.searchParams.get("languages"), "en");
      const empty =
        collection === "seo-empty" || requestUrl.searchParams.get("content_type") === "tutorial";
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ items: empty ? [] : [article], next_cursor: null }));
      return;
    }
    if (mode === "default-all" && path === "/v1/feed") defaultAllRequests.push(requestUrl);
    const pendingFeed = articleFeedGate;
    if (pendingFeed && (await pendingFeed.wait(path)) && pendingFeed.fail) {
      res.writeHead(503, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Feed unavailable" }));
      return;
    }
    const promptArticle = path.startsWith("/v1/articles/")
      ? signupPromptArticle(article, path.slice("/v1/articles/".length))
      : null;
    if (promptArticle) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(promptArticle));
      return;
    }
    if (path.startsWith("/v1/user/must-reads")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(await mustReadsFixture.response(path.endsWith("/presentation"))));
      return;
    }
    if (path === "/v1/user/profiles/promo-reader") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(promoPublicProfile));
      return;
    }
    if (path === "/v1/user/profiles/promo-reader/reading-heatmap") {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "No activity" }));
      return;
    }
    if (path === "/v1/articles/snippet-article") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(snippetArticle(article)));
      return;
    }
    if (path === `/v1/articles/${article.slug}`) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(article));
      return;
    }
    if (path === "/v1/articles/old") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ...article, slug: "old" }));
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
    if (mode === "engagement-pagination" && ["/v1/feed", "/v1/user/engagement"].includes(path)) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify(
          path === "/v1/feed"
            ? engagementFeed(article, requestUrl.searchParams)
            : engagementRows(requestUrl.searchParams),
        ),
      );
      return;
    }
    const authenticated = req.headers.cookie?.includes("devfeed_user_session=valid");
    if (path === "/v1/search/analytics/click") {
      assert.equal(req.method, "POST");
      assert.equal(req.headers["content-type"], "application/json");
      assert.equal(req.headers.cookie, undefined);
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      searchClicks.push(JSON.parse(Buffer.concat(chunks).toString()));
      res.writeHead(204);
      res.end();
      return;
    }
    if (path === "/v1/search") {
      if (requestUrl.searchParams.has("sort"))
        await new Promise((resolve) => setTimeout(resolve, 800));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify(
          searchFixture(
            requestUrl.searchParams.get("q"),
            requestUrl.searchParams.get("sort"),
            requestUrl.searchParams.get("content_types"),
            requestUrl.searchParams.get("section"),
          ),
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
    else if (path === "/v1/user/profiles/leader-reader") body = leaderboardProfile;
    else if (path === "/v1/user/profiles/leader-reader/reading-heatmap") body = null;
    else if (path === "/v1/user/auth/config") body = { enabled: true, providers: [] };
    else if (path === "/v1/user/settings/profile")
      body = {
        display_name: "Reader",
        avatar_url: null,
        reading_streak: readingFixture.profile(),
      };
    else if (path === "/v1/user/settings/reading-week") body = readingFixture.response();
    else if (path === "/v1/user/settings/appearance") body = { theme: "light" };
    else if (path === "/v1/user/settings/notifications") body = { show_badge: true, sound: false };
    else if (path === "/v1/user/notifications/config")
      body = { enabled: true, environment: "users", subscriber_id: "user_a" };
    else if (path.endsWith("/items") && path.includes("/notifications/chimely/"))
      body = notificationFixture();
    else if (path.endsWith("/counts") && path.includes("/notifications/chimely/"))
      body = { unread: 1, unseen: notificationCount };
    else if (path.endsWith("/stream") && path.includes("/notifications/chimely/")) {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.end(": connected\n\n");
      return;
    } else if (path === "/v1/user/settings/feed") {
      if (req.method === "PUT") {
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        feedSettings = JSON.parse(Buffer.concat(chunks).toString());
      }
      body = feedSettings;
    } else if (path === "/v1/feed/options")
      body = {
        sources: [source, longFilterSource],
        content_types:
          mode === "default-all"
            ? ["article", "news", "tutorial", "release", "comparison", "opinion"]
            : ["article"],
        languages: ["en"],
      };
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
    else if (path === `/v1/sources/${source.slug}`) body = source;
    else if (path === "/v1/sources") body = onboardingSources;
    else if (path === "/v1/user/preferences/sources") body = { source_ids: savedSourceIds };
    else if (path.startsWith("/v1/user/preferences/sources/") && req.method === "PUT") {
      assert.ok(authenticated);
      assert.equal(req.headers["x-csrf-token"], "test");
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString());
      assert.deepEqual(Object.keys(payload), ["followed"]);
      if (payload.followed && rejectSourceFollow) {
        rejectSourceFollow = false;
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end("{}");
        return;
      }
      const id = path.split("/").at(-1);
      savedSourceIds = payload.followed
        ? [...new Set([...savedSourceIds, id])]
        : savedSourceIds.filter((value) => value !== id);
      body = { followed: payload.followed };
    } else if (path === "/v1/user/engagement")
      body = [{ article_id: article.id, likes: 0, liked: false, opens: 0, bookmarked }];
    else if (path.startsWith("/v1/user/articles/") && path.endsWith("/bookmark")) {
      assert.ok(authenticated);
      assert.equal(req.method, "PUT");
      assert.equal(req.headers["x-csrf-token"], "test");
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString());
      assert.deepEqual(Object.keys(payload), ["bookmarked"]);
      assert.equal(typeof payload.bookmarked, "boolean");
      const articleId = path.split("/").at(-2);
      if (payload.bookmarked) bookmarkedArticleIds.add(articleId);
      else bookmarkedArticleIds.delete(articleId);
      if (articleId === article.id) bookmarked = payload.bookmarked;
      body = { article_id: articleId, bookmarked: payload.bookmarked };
    } else if (path === `/v1/user/articles/${article.id}/like`) {
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
    else if (path === "/v1/user/trending") body = { items: [article], next_cursor: null };
    else if (path === "/v1/user/bookmarks") body = { items: [], next_cursor: null };
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
            generation:
              requestUrl.searchParams.get("generation") ?? (mode === "new" ? "new" : "old"),
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
      body =
        mode === "default-all"
          ? defaultAllFeed(article, requestUrl.searchParams)
          : {
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
                  : thumbnailFeed
                    ? Array.from({ length: 12 }, (_, index) => ({
                        ...article,
                        id: `image-${index}`,
                        slug: `image-${index}`,
                        image_url: article.image_url.replace("/fixture/", `/fixture-${index}/`),
                        image_variants: article.image_variants.map((variant) => ({
                          ...variant,
                          url: variant.url.replace("/fixture/", `/fixture-${index}/`),
                        })),
                      }))
                    : [article],
              next_cursor:
                mode === "scroll" && !requestUrl.searchParams.has("cursor") ? "next+page" : null,
            };
    if (
      compactArticles &&
      Array.isArray(body?.items) &&
      ["/v1/feed", "/v1/user/feed", "/v1/user/trending"].includes(path)
    )
      body = { ...body, items: body.items.map(compactListArticle) };
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(body));
  });
  await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
  userFixture = createServer((req, res) => fixture.emit("request", req, res));
  await new Promise((resolve) => userFixture.listen(0, "127.0.0.1", resolve));
  const probe = createServer();
  await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const upstream = `http://127.0.0.1:${fixture.address().port}`;
  const userUpstream = `http://127.0.0.1:${userFixture.address().port}`;
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
      cwd: `${root}/apps/web`,
      env: {
        ...env,
        DEVFEED_PUBLIC_API_URL: upstream,
        DEVFEED_MCP_PUBLIC_URL: testMcpEndpoint,
        DEVFEED_USER_API_URL: userUpstream,
        DEVFEED_USER_BASE_URL: origin,
      },
      stdio: "pipe",
    },
  );
  app.stdout.on("data", (data) => (logs += data));
  app.stderr.on("data", (data) => (logs += data));
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (app.exitCode !== null || app.signalCode !== null) break;
    try {
      await fetch(`${origin}/login`, { redirect: "manual" });
      ready = true;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  assert.ok(ready, `Production reader did not become ready.\n${logs}`);
  browser = await chromium.launch({ headless: true });
  async function runJourney() {
    const seoPage = await browser.newPage();
    for (const kind of ["tags", "topics"]) {
      for (const [slug, suffix, empty] of [
        ["seo-populated", "", false],
        ["seo-empty", "", true],
        ["seo-populated", "/tutorials", true],
      ]) {
        const response = await seoPage.goto(`${origin}/${kind}/${slug}${suffix}`);
        assert.equal(response.status(), 200);
        const robots = (await seoPage.locator('meta[name="robots"]').getAttribute("content")) || "";
        assert.equal(robots.includes("noindex"), empty);
        if (empty)
          await seoPage.getByText("No articles match these filters", { exact: true }).waitFor();
        else await seoPage.locator(`a[href="/articles/${article.slug}"]`).first().waitFor();
      }
      const missing = await fetch(`${origin}/${kind}/seo-missing`, {
        headers: { "User-Agent": "Googlebot" },
      });
      assert.equal(missing.status, 404);
    }
    await seoPage.close();
    for (const path of ["/latest?sort=newest", "/api/v1/feed?sort=newest"]) {
      const redirect = await new Promise((resolve, reject) => {
        const request = httpRequest(
          {
            hostname: "127.0.0.1",
            port,
            path,
            method: "HEAD",
            headers: { Host: "devfeed.tech", "X-Forwarded-Proto": "http" },
          },
          (response) => {
            response.resume();
            response.on("end", () => resolve(response));
          },
        );
        request.on("error", reject);
        request.end();
      });
      assert.equal(redirect.statusCode, 308);
      assert.equal(redirect.headers.location, `https://devfeed.tech${path}`);
    }
    const context = await readerBrowserContext(browser, {
      viewport: { width: 1440, height: 1000 },
      reducedMotion: "reduce",
    });
    await mockManagedImages(context);
    await mockSourceLogos(context);
    const prefetchedRoutes = [];
    context.on("request", (request) => {
      if (request.headers()["next-router-prefetch"] === "1") prefetchedRoutes.push(request.url());
    });
    await checkNonceCsp(context, `${origin}/latest`);
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__cspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
      });
    });
    mode = "default-all";
    await checkDefaultAll(
      page,
      origin,
      `${root}/reports/default-all/web`,
      () => defaultAllRequests,
    );
    mode = "ready";
    mode = "engagement-pagination";
    await checkEngagementPagination(page, `${origin}/latest`);
    mode = "ready";
    const campaign = "utm_source=linkedin&utm_medium=organic&utm_campaign=reader_updates";
    await page.goto(`${origin}/?${campaign}&unrelated=discard`);
    await page.waitForURL(`${origin}/?${campaign}&unrelated=discard`);
    await checkManagedImages(page);
    await checkSourceLogos(page);
    await checkSourceLogoPages(page);

    await checkPreviewCover(
      context,
      `${origin}/articles/${article.slug}`,
      `${root}/reports/preview-cover/web`,
    );
    await checkArticleGrid(page, `${root}/reports/reader-feed/grid-web`);
    await checkArticleViews(
      page,
      `${origin}/latest`,
      `${root}/reports/reader-feed/article-view`,
      [`${origin}/topics/${topic.slug}`, `${origin}/sources/${source.slug}`, `${origin}/trending`],
      {
        fixture: (active) => {
          compactArticles = active;
        },
      },
    );
    await page.goto(`${origin}/latest`);
    const onboarding = page.getByRole("dialog", {
      name: "DevFeed is your daily briefing on what’s next.",
    });
    assert.equal(
      await onboarding.count(),
      0,
      "Anonymous visitors do not receive a welcome-tour modal",
    );
    await mkdir(`${root}/reports/reader-feed`, { recursive: true });
    await page.reload();
    await page.getByRole("heading", { name: "Latest feed", exact: true }).waitFor();
    const policyResponse = await page.request.get(`${origin}/latest`);
    const csp = policyResponse.headers()["content-security-policy"];
    assert.match(csp, /script-src [^;]*'nonce-[^']+'/);
    assert.doesNotMatch(csp, /script-src [^;]*'unsafe-inline'/);
    assert.equal(policyResponse.headers()["x-frame-options"], undefined);
    assert.ok(csp.split("; ").includes("frame-ancestors 'self' https://nicklaunches.com"));
    assert.equal(policyResponse.headers()["strict-transport-security"], "max-age=31536000");
    assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
    assert.equal(await onboarding.count(), 0);
    await checkAccessibility(page, "web");
    await checkReaderNavigation(page, `${root}/reports/mobile-navigation/web`);
    assert.equal(await page.locator(".mobile-nav").getByRole("link", { name: "Legal" }).count(), 0);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await checkLeaderboard(page, `${root}/reports/reader-feed/leaderboard-guest`);
    await checkDevCardPromo(page, `${root}/reports/reader-feed/dev-card-promo`);
    await checkSignupPrompts(context, origin, `${root}/reports/reader-feed/signup`);
    await page.bringToFront();
    await checkSidebarGitHub(page, `${root}/reports/reader-feed/sidebar-github.png`);
    await checkMcp(page, `${root}/reports/reader-feed/mcp`);
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

    assert.equal(
      await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(),
      0,
    );
    assert.equal(
      await page.locator(".mobile-nav").getByRole("link", { name: "Read later" }).count(),
      0,
    );
    await context.addCookies([{ name: "devfeed_user_session", value: "expired", url: origin }]);
    await page.goto(origin);
    await page.locator(".article-card").first().waitFor();
    assert.equal(new URL(page.url()).pathname, "/");
    const topicPath = `/topics/${topic.slug}/articles?language=en`;
    await page.goto(`${origin}${topicPath}`);
    const guestFollow = page
      .getByRole("region", { name: "Feed controls" })
      .getByRole("link", { name: "Follow", exact: true });
    await guestFollow.waitFor();
    assert.equal(
      await guestFollow.getAttribute("href"),
      `/login?return_to=${encodeURIComponent(topicPath.split("?")[0])}`,
    );
    await checkGuestTopicSignIn(
      page,
      `${origin}/topics/${topic.slug}`,
      false,
      `${upstream}/authorize`,
    );
    await context.addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
    const navigationPage = await context.newPage();
    const navigationMustReads = {
      enabled: mustReadsFixture.enabled,
      presented: mustReadsFixture.presented,
    };
    try {
      mustReadsFixture.enabled = true;
      mustReadsFixture.presented = true;
      for (const count of [1, 123]) {
        notificationCount = count;
        await navigationPage.goto(origin);
        await navigationPage
          .getByRole("button", {
            name: count === 123 ? "Notifications, 99+ new (123 in total)" : "Notifications, 1 new",
            exact: true,
          })
          .waitFor();
        await checkReaderNavigation(
          navigationPage,
          `${root}/reports/mobile-navigation/web/count-${count}`,
          { signedIn: true },
        );
      }
    } finally {
      notificationCount = 1;
      Object.assign(mustReadsFixture, navigationMustReads);
      await navigationPage.close();
    }
    await page.addInitScript(() => {
      Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false });
    });
    await page.goto(origin);
    await page.getByRole("region", { name: "Feed controls" }).waitFor();
    await checkUnclaimedDevCardPromo(page, `${root}/reports/reader-feed/dev-card-promo`);
    await checkExtensionInstall(
      page,
      "chrome",
      ".feed-toolbar-actions",
      `${root}/reports/reader-feed/install-button-personal.png`,
    );
    assert.equal(
      await page
        .locator(".sidebar .shared-sidebar-content > nav")
        .first()
        .getByRole("link")
        .first()
        .innerText(),
      "My feed",
    );
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).waitFor();
    await checkAccessibility(page, "web", { signedIn: true });
    assert.equal(new URL(page.url()).pathname, "/");
    assert.equal(await page.locator(".article-grid").count(), 1);
    assert.equal(await page.locator(".article-card").count(), 1);
    assert.equal(
      await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(),
      1,
    );
    assert.equal(await page.evaluate(() => document.hasFocus()), false);
    mode = "new";
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    assert.equal(
      await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
      1,
    );
    await checkPreviewBackground(page, `${root}/reports/reader-feed/personal-preview.png`);
    // Exercise likes on a wide card; narrow cards intentionally only show bookmarks.
    await page.setViewportSize({ width: 520, height: 1000 });
    const requestsBeforeLike = personalFeedRequests;
    const existingCard = await page.locator(".article-card").first().elementHandle();
    await page.getByRole("button", { name: /^Like article/ }).click();
    await page.getByRole("button", { name: /^Unlike article/ }).waitFor();
    await page.getByRole("button", { name: /^Unlike article/ }).click();
    await page.getByRole("button", { name: /^Like article/ }).waitFor();
    assert.equal(personalFeedRequests, requestsBeforeLike);
    assert.equal(await existingCard.evaluate((node) => node.isConnected), true);
    await page.setViewportSize({ width: 1440, height: 1000 });
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
    assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
    await checkFeedSort(page, origin);
    await checkSourceFilter(page, `${origin}/latest`, source, `${output}/web-filter`);
    await checkFeedSort(page, origin, true);
    // Layout assertions use a settled feed; the refreshing fixture deliberately polls.
    mode = "ready";
    await checkArticleViews(page, `${origin}/`, `${root}/reports/reader-feed/personal-view`, [
      `${origin}/latest`,
      `${origin}/`,
    ]);
    await checkCompactLoading(
      page,
      `${origin}/`,
      `${output}/web-compact`,
      compactLoadingGate(page),
    );
    mode = "refreshing";
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
    await onboardingPage.clock.install();
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
    await checkSourceFollow(page, `${origin}/sources`, onboardingSources, `${output}/web`);
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
    await checkMustReads(scrollPage, mustReadsFixture, `${root}/reports/reader-feed/must-reads`);
    assert.equal(
      [...bookmarkedArticleIds].filter((id) => id !== article.id).length,
      5,
      "Must Reads saves all five distinct unread picks",
    );
    await checkReadingStreak(scrollPage, `${root}/reports/reader-feed/reading-streak`, {
      fixture: readingFixture,
      mustReads: mustReadsFixture,
    });
    await scrollPage.locator(".pagination").scrollIntoViewIfNeeded();
    await scrollPage
      .getByRole("heading", { name: "Automatically appended feed article" })
      .waitFor();
    assert.equal(await scrollPage.locator(".article-card").count(), 25);
    assert.equal(await scrollPage.locator(".article-grid").count(), 1);
    mode = "ready";
    await checkSearchInfiniteScroll(scrollPage, `${origin}/search?q=infinite-scroll`);
    mode = "catalog-scroll";
    await checkCatalogScroll(scrollPage, origin, `${root}/reports/reader-feed/catalog-web`);
    mode = "ready";
    await checkReaderInteractions(
      scrollPage,
      (path) => origin + path,
      `${root}/reports/reader-feed/mobile-interactions.png`,
      { recover: true },
    );
    await checkLeaderboard(scrollPage, `${root}/reports/reader-feed/leaderboard-signed-in`, {
      signedIn: true,
    });
    await scrollPage.close();
    await checkSearchFilters(page, `${origin}/search?q=microservice`);
    const clicksBeforeSecurity = searchClicks.length;
    await checkSearchSecurity(
      page,
      `${origin}/search?${new URLSearchParams({ q: searchSecurityQuery })}`,
      { relayClick: true },
    );
    assert.deepEqual(searchClicks.slice(clicksBeforeSecurity), [searchSecurityEvent]);
    const edgeContext = await readerBrowserContext(browser, {
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
      viewport: { width: 1440, height: 1000 },
    });
    await edgeContext.addInitScript(() => {
      // This context checks the store button; the modal has its own shared coverage.
      sessionStorage.setItem("devfeed:dev-card-promo-dismissed", "true");
    });
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
      ".feed-toolbar-actions",
      `${output}/install-button-edge-personal.png`,
    );
    await edgeContext.close();
    const preparationPage = await context.newPage();
    await checkFeedPreparation(preparationPage, origin, article, `${output}/preparation`);
    await preparationPage.close();
    for (const fail of [false, true]) {
      articleFeedGate = blockedFeed(fail);
      const directContext = await readerBrowserContext(browser, {
        viewport: fail ? { width: 412, height: 823 } : { width: 1440, height: 1000 },
      });
      const directPage = await directContext.newPage();
      try {
        await checkArticleFirst(directPage, `${origin}/articles/${article.slug}`, articleFeedGate, {
          apple: true,
          serverEntry: true,
          screenshot: `${root}/reports/article-first-web-${fail ? "failed" : "slow"}-feed.png`,
        });
        if (!fail) await directPage.waitForURL(`${origin}/`);
        assert.equal(
          await directPage.locator('link[rel="canonical"]').getAttribute("href"),
          fail ? `${origin}/articles/${article.slug}` : `${origin}/latest`,
        );
      } finally {
        articleFeedGate.release();
        articleFeedGate = undefined;
        await directContext.close();
      }
    }
    const earlyContext = await readerBrowserContext(browser, {
      viewport: { width: 412, height: 823 },
    });
    const earlyPage = await earlyContext.newPage();
    await earlyPage.route(/\/_next\/static\/.*\.js(?:\?.*)?$/, (route) => route.abort());
    await earlyPage.goto(`${origin}/articles/${article.slug}`, { waitUntil: "domcontentloaded" });
    await earlyPage.locator("dialog:modal .preview-summary").waitFor();
    await earlyPage.keyboard.press("Escape");
    await earlyPage.waitForURL(`${origin}/`);
    await earlyContext.close();
    const retryContext = await readerBrowserContext(browser, {
      viewport: { width: 390, height: 844 },
    });
    const retryPage = await retryContext.newPage();
    await checkArticleBeforeHydration(
      retryPage,
      `${origin}/articles/retry-article`,
      ".empty-state h1",
      `${root}/reports/article-unavailable-before-hydration.png`,
    );
    await retryPage.getByRole("heading", { name: "Couldn’t load the article" }).waitFor();
    assert.ok(
      (await retryPage.locator('meta[name="robots"]').getAttribute("content")).includes("noindex"),
    );
    await retryPage.screenshot({ path: `${root}/reports/article-retry-web.png` });
    failArticle = false;
    await retryPage.getByRole("button", { name: "Try again", exact: true }).click();
    await retryPage.locator("#article-preview-title").waitFor();
    await retryContext.close();
    assert.deepEqual(prefetchedRoutes, [], "Reader navigation must not prefetch unvisited pages");
    console.log(
      "Reader routes, signed-out navigation, and background recommendation refresh passed.",
    );
  }
  return {
    browser,
    origin,
    youtube,
    upstream,
    userUpstream,
    serverReads,
    loginDestinations,
    readerDestinations,
    readerContext,
    feedMode,
    routeFixture,
    mutationFixture,
    runJourney,
    domCatalog,
    renderReads,
    topicContext,
    setArticleFeedGate: (gate) => {
      articleFeedGate = gate;
    },
  };
}

async function readerBrowserContext(browser, options = {}) {
  const context = await browser.newContext(options);
  // Keep production analytics markup and first-party event assertions, but do
  // not download a changing third-party SDK or send fixture traffic to Google.
  await context.route("https://www.googletagmanager.com/**", (route) =>
    route.fulfill({ contentType: "text/javascript", body: "window.gtag = () => {};" }),
  );
  return context;
}

async function withReaderPage(browser, check, options = {}) {
  const context = await readerBrowserContext(browser, { reducedMotion: "reduce", ...options });
  try {
    await mockManagedImages(context);
    await mockSourceLogos(context);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && /Content Security Policy|Refused to/.test(message.text()))
        errors.push(message.text());
    });
    await check(page, context);
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
  }
}

test("source and topic sharing uses public collection links", { timeout: 180000 }, async (t) => {
  const { browser, origin } = await createReaderFixture(t);
  await withReaderPage(browser, (page) =>
    checkCatalogSharing(
      page,
      (path) => origin + path,
      [
        { kind: "source", path: "/sources/example-source" },
        { kind: "topic", path: "/topics/typescript" },
      ],
      "/tmp/devfeed-catalog-sharing/web",
      origin,
    ),
  );
});

test(
  "keyboard access preserves native links and readable scroll regions",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin } = await createReaderFixture(t);
    await withReaderPage(
      browser,
      (page) => checkKeyboardReader(page, (path) => origin + path, "/tmp/devfeed-sonar-a11y/web"),
      { viewport: { width: 1280, height: 1000 } },
    );
  },
);

test("thumbnails match rendered widths and device pixel ratios", { timeout: 360000 }, async (t) => {
  const { browser, origin } = await createReaderFixture(t, { thumbnailFeed: true });
  const results = [];
  for (const profile of thumbnailProfiles) {
    for (const sidebar of ["collapsed", "expanded"]) {
      await withReaderPage(
        browser,
        async (page) =>
          results.push(...(await checkThumbnailSizing(page, `${origin}/latest`, sidebar))),
        {
          viewport: { width: profile.width, height: profile.height },
          deviceScaleFactor: profile.dpr,
        },
      );
    }
  }
  await mkdir(`${root}/reports/thumbnail-sizing`, { recursive: true });
  const { writeFile } = await import("node:fs/promises");
  await writeFile(`${root}/reports/thumbnail-sizing/web.json`, JSON.stringify(results, null, 2));
});

test(
  "YouTube videos retain metadata and shared reader interactions",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, youtube } = await createReaderFixture(t);
    await withReaderPage(browser, (page) =>
      checkYoutubeReader(page, (path) => origin + path, youtube, "web"),
    );
  },
);
test("article topic panels preserve contextual assignments", { timeout: 180000 }, async (t) => {
  const { browser, origin, topicContext } = await createReaderFixture(t);
  for (const item of topicContext.cases) {
    const response = await fetch(`${origin}/articles/${item.article.slug}`);
    assert.equal(response.status, 200);
    const dom = new JSDOM(await response.text());
    checkArticleTopicDocument(dom.window.document, item);
    dom.window.close();
  }
  await withReaderPage(browser, (page) =>
    checkArticleTopicContext(
      page,
      (path) => origin + path,
      topicContext,
      `${process.env.DEVFEED_TOPIC_CONTEXT_REPORT_DIR ?? `${root}/reports/article-topic-context`}/web`,
    ),
  );
});

test("article snippets exclude unrelated content", { timeout: 180000 }, async (t) => {
  const { browser, origin } = await createReaderFixture(t);
  const href = `${origin}/articles/snippet-article`;
  for (const userAgent of ["Googlebot", "bingbot"]) {
    const response = await fetch(href, { headers: { "User-Agent": userAgent } });
    assert.equal(response.status, 200);
    const dom = new JSDOM(await response.text());
    checkSnippetDocument(dom.window.document, true);
    dom.window.close();
  }
  await withReaderPage(browser, (page) => checkArticleSnippets(page, href));
});

test(
  "server reads retain explicit transport and sitemap policies",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, upstream, userUpstream, serverReads } = await createReaderFixture(t);
    await withReaderPage(browser, async (page, context) => {
      await checkServerReads(
        page,
        (path) => origin + path,
        serverReads,
        `${root}/reports/server-reads/web`,
        { publicOrigin: upstream, userOrigin: userUpstream },
      );
      await checkSitemapPolicy(context.request, origin, serverReads);
      await checkLegalMetadata(page, origin);
    });
  },
);

test("login destinations preserve supported reader routes", { timeout: 180000 }, async (t) => {
  const { browser, origin, upstream, loginDestinations } = await createReaderFixture(t);
  await withReaderPage(browser, (page) =>
    checkLoginDestinations(
      page,
      (path) => origin + path,
      loginDestinations,
      `${root}/reports/login-destinations/web`,
      { provider: upstream + "/authorize" },
    ),
  );
});

test(
  "reader navigation preserves typed destinations and overlays",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, readerDestinations } = await createReaderFixture(t);
    await withReaderPage(browser, (page) =>
      checkReaderDestinations(
        page,
        (path) => origin + path,
        readerDestinations,
        `${root}/reports/reader-destinations/web`,
      ),
    );
  },
);

test("reader context controls article prompts", { timeout: 180000 }, async (t) => {
  const { browser, origin, readerContext } = await createReaderFixture(t);
  await withReaderPage(browser, (page) =>
    checkReaderContext(
      page,
      (path) => origin + path,
      readerContext,
      `${root}/reports/reader-context/web`,
    ),
  );
});

test("feed modes declare placement capabilities", { timeout: 180000 }, async (t) => {
  const { browser, origin, feedMode } = await createReaderFixture(t);
  await withReaderPage(browser, (page) =>
    checkFeedMode(page, (path) => origin + path, feedMode, `${root}/reports/feed-mode/web`),
  );
});

test(
  "reader routes validate destinations and recover from missing pages",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, routeFixture } = await createReaderFixture(t);
    await withReaderPage(browser, (page) =>
      checkReaderRoutes(
        page,
        (path) => origin + path,
        routeFixture,
        `${root}/reports/reader-routes/web`,
      ),
    );
  },
);

test(
  "user mutations update shared state after successful writes",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, mutationFixture } = await createReaderFixture(t);
    await withReaderPage(browser, async (page, context) => {
      await context.addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
      await checkUserMutations(page, (path) => origin + path, mutationFixture);
    });
  },
);

test("reader theme persists and follows system settings", { timeout: 180000 }, async (t) => {
  const { browser, origin } = await createReaderFixture(t);
  await withReaderPage(
    browser,
    async (page) => {
      await page.goto(`${origin}/latest`);
      await page.locator(".article-card").first().waitFor();
      await mkdir(`${root}/reports/reader-feed`, { recursive: true });
      await checkBrowserTheme(page, `${root}/reports/reader-feed/shared-theme`, {
        bootstrap: true,
      });
    },
    { viewport: { width: 1440, height: 900 } },
  );
});

test("reader sidebar persists and adapts to the viewport", { timeout: 180000 }, async (t) => {
  const { browser, origin } = await createReaderFixture(t);
  await withReaderPage(
    browser,
    async (page) => {
      await page.goto(`${origin}/latest`);
      await page.locator(".article-card").first().waitFor();
      await mkdir(`${root}/reports/reader-feed`, { recursive: true });
      await checkCollapsibleSidebar(page, `${root}/reports/reader-feed/shared-sidebar`);
    },
    { viewport: { width: 1440, height: 900 } },
  );
});

test("reader journey works at desktop and mobile sizes", { timeout: 360000 }, async (t) => {
  const { runJourney } = await createReaderFixture(t);
  await runJourney();
});

test("route styles load before reader surfaces paint", { timeout: 180000 }, async (t) => {
  const { browser, origin } = await createReaderFixture(t);
  await withReaderPage(browser, (page) => checkRouteStyles(page, (path) => origin + path));
});
test(
  "reader DOM stays bounded per card while retaining long catalogs",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, domCatalog } = await createReaderFixture(t);
    await withReaderPage(browser, (page) =>
      checkReaderDom(page, (path) => origin + path, domCatalog, `${root}/reports/reader-dom/web`),
    );
  },
);

test(
  "guest document streams before results and render reads stay isolated",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin, setArticleFeedGate, renderReads } = await createReaderFixture(t);
    await withReaderPage(browser, async (page, context) => {
      await checkGuestDocument(
        page,
        (path) => origin + path,
        setArticleFeedGate,
        `${root}/reports/document-latency/web`,
      );
      await checkRenderReads(context.request, origin, renderReads);
    });
  },
);

test(
  "reader framing headers cover every route and allow only the configured ancestors",
  { timeout: 180000 },
  async (t) => {
    const { browser, origin } = await createReaderFixture(t);
    const context = await readerBrowserContext(browser);
    try {
      await mockManagedImages(context);
      await mockSourceLogos(context);
      const probe = await context.newPage();
      await probe.goto(origin + "/latest");
      const staticScript = await probe
        .locator('script[src^="/_next/static/"]')
        .first()
        .getAttribute("src");
      await probe.close();
      for (const pathname of [
        staticScript,
        "/",
        "/latest",
        "/login",
        "/robots.txt",
        "/llms.txt",
        "/sitemap.xml",
        "/favicon.ico",
        "/api/v1/mcp/config",
        "/api/v1/users/leader-reader/card.svg",
        "/metrics",
        "/route-does-not-exist",
      ]) {
        for (const method of ["GET", "HEAD"]) {
          const response = await context.request.fetch(origin + pathname, { method });
          const headers = response.headers();
          assert.equal(headers["x-frame-options"], undefined, pathname);
          assert.ok(
            headers["content-security-policy"]
              ?.split("; ")
              .includes("frame-ancestors 'self' https://nicklaunches.com"),
            `Missing framing policy on ${pathname}`,
          );
          assert.equal(headers["strict-transport-security"], "max-age=31536000", pathname);
          assert.equal(headers["x-content-type-options"], "nosniff", pathname);
          assert.equal(
            headers["permissions-policy"],
            "camera=(), microphone=(), geolocation=(), payment=()",
            pathname,
          );
          if (pathname.endsWith("card.svg")) {
            assert.equal(response.status(), 200);
            assert.ok(
              headers["content-security-policy"].includes(
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox",
              ),
            );
          }
        }
      }
      await checkNonceCsp(context, origin + "/latest");
      await context.route(
        /^https:\/\/(?:devfeed\.tech|nicklaunches\.com|www\.nicklaunches\.com|blocked\.example\.test)\//,
        async (route) => {
          const url = new URL(route.request().url());
          if (url.pathname === "/iframe-host")
            return route.fulfill({
              contentType: "text/html",
              body: '<iframe src="https://devfeed.tech/latest" title="Reader"></iframe>',
            });
          const response = await route.fetch({ url: origin + url.pathname + url.search });
          await route.fulfill({ response });
        },
      );
      for (const parent of ["https://devfeed.tech", "https://nicklaunches.com"]) {
        const page = await context.newPage();
        await page.goto(parent + "/iframe-host");
        await page
          .frameLocator("iframe")
          .getByRole("heading", { name: "Latest feed", exact: true })
          .waitFor();
        assert.equal(
          await page.evaluate(() => {
            try {
              return !!document.querySelector("iframe").contentDocument;
            } catch {
              return false;
            }
          }),
          parent === "https://devfeed.tech",
          "Framing must preserve the reader's origin",
        );
        await page.close();
      }
      for (const parent of ["https://www.nicklaunches.com", "https://blocked.example.test"]) {
        const page = await context.newPage();
        const refused = page.waitForEvent("console", {
          predicate: (message) => /frame-ancestors/.test(message.text()),
        });
        await page.goto(parent + "/iframe-host");
        await refused;
        assert.equal(
          await page
            .frameLocator("iframe")
            .getByRole("heading", { name: "Latest feed", exact: true })
            .count(),
          0,
        );
        await page.close();
      }
    } finally {
      await context.close();
    }
  },
);
