import { checkArticleViews, compactLoadingGate } from "../../web/tests/browser/article-views.mjs";
import { checkCompactLoading } from "../../../scripts/testing/compact-loading.mjs";
import {
  checkArticleViewSettings,
  readerRouteUrl,
} from "../../../scripts/testing/article-view.mjs";
import { checkReaderPreferences } from "../../../scripts/testing/reader-preferences.mjs";
import { createFixtureFeedViews } from "../../../scripts/testing/feed-view-fixture.mjs";
import {
  checkColumnsWorkspace,
  checkGuestColumnsUnavailable,
} from "../../../scripts/testing/columns-workspace.mjs";
import { checkColumnsFeeds } from "../../../scripts/testing/columns-feeds.mjs";
import { checkColumnsSponsoredProducts } from "../../../scripts/testing/columns-sponsored.mjs";
import { checkColumnsScanning } from "../../../scripts/testing/columns-scanning.mjs";
import { checkColumnsReordering } from "../../../scripts/testing/columns-reordering.mjs";
import { createReaderArtifactDirectory } from "../../../scripts/testing/reader-artifacts.mjs";
import { checkIndependentFeedViews } from "../../../scripts/testing/feed-views.mjs";
import { checkSavedReaderLifecycle } from "../../../scripts/testing/reader-views-lifecycle.mjs";
import { checkColumnSaveRecovery } from "../../../scripts/testing/reader-views-recovery.mjs";
import { checkSavedReaderAccessibility } from "../../../scripts/testing/reader-views-accessibility.mjs";
import {
  readerViewsReportDirectory,
  runReaderViewCheck,
} from "../../../scripts/testing/reader-views-evidence.mjs";
import { checkColumnsStarters } from "../../../scripts/testing/columns-starters.mjs";
import { checkPersonalColumns } from "../../../scripts/testing/columns-personal.mjs";
import {
  checkPreviewFeedRetention,
  previewRetentionArticle,
  previewRetentionFeed,
  previewRetentionEngagement,
} from "../../../scripts/testing/preview-feed-retention.mjs";
import { checkSessionPreferences } from "../../../scripts/testing/session-preferences.mjs";
import {
  checkStackedReader,
  checkStackedTouchReader,
  stackedReaderArticle,
  stackedReaderFeed,
} from "../../../scripts/testing/stacked-reader.mjs";
import { withManagedImage, mockManagedImages } from "../../../scripts/testing/managed-images.mjs";
import { checkSelectMenus } from "../../../scripts/testing/select-menus.mjs";
import { checkAccessibility } from "../../web/tests/browser/accessibility.mjs";
import { dailyFixture, checkMustReads } from "../../../scripts/testing/must-reads.mjs";
import { checkAvatarUploads } from "../../../scripts/testing/avatar-uploads.mjs";
import { checkLeaderboard } from "../../web/tests/browser/leaderboard.mjs";
import {
  checkReadingStreak,
  readingStreakFixture,
} from "../../../scripts/testing/reading-streak.mjs";
import { checkReaderNavigation } from "../../../scripts/testing/reader-navigation.mjs";
import {
  checkReaderInteractions,
  notificationFixture,
} from "../../web/tests/browser/reader-interactions.mjs";
import {
  checkDevCardPromo,
  checkUnclaimedDevCardPromo,
} from "../../../scripts/testing/dev-card-promo.mjs";
import { checkPreviewBackground } from "../../../scripts/testing/preview-background.mjs";
import { checkProfileEditor } from "../../../scripts/testing/profile-editor.mjs";
import { checkDevCard } from "../../../scripts/testing/dev-card.mjs";
import { checkFeedPreparation } from "../../../scripts/testing/feed-preparation.mjs";
import {
  checkLanguagePreferences,
  checkFeedSort,
} from "../../../scripts/testing/feed-preferences.mjs";
import { catalogChoices, checkCatalogScroll } from "../../../scripts/testing/catalog-scroll.mjs";
import { createServer } from "node:https";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { checkFeedOnboarding } from "../../web/tests/browser/feed-onboarding.mjs";
import {
  checkSignupPrompts,
  signupPromptArticle,
} from "../../web/tests/browser/signup-prompts.mjs";
import { checkTopicFollow } from "../../../scripts/testing/topic-follow.mjs";
import { checkSourceFollow } from "../../web/tests/browser/source-follow.mjs";
import {
  onboardingTopics,
  onboardingSources,
} from "../../../scripts/testing/onboarding-topics.mjs";

const browser = process.env.DEVFEED_EXTENSION_BROWSER ?? "chrome";
const artifactDirectory = await createReaderArtifactDirectory(browser);
const artifactPath = (name) => path.join(artifactDirectory, name);
const extension = path.resolve(import.meta.dirname, `../dist/${browser}`);
const newTab = browser === "edge" ? "edge://newtab" : "chrome://newtab";
const cookieName = "__Host-devfeed_user_session";
const xClickCookie = "__Host-devfeed_user_x_click";
const topicId = "55555555-5555-4555-8555-555555555555";
const baseUser = {
  user_id: "11111111-1111-4111-8111-111111111111",
  name: "Test Reader",
  email: "reader@example.test",
  // Production sessions last 30 days, beyond the browser's ~24.8-day timer limit.
  expires_at: Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60,
  csrf_token: "c".repeat(43),
};
const baseArticle = {
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

async function withAuthenticatedReader(t, check, { hasTouch = false } = {}) {
  const profile = await mkdtemp(path.join(tmpdir(), "devfeed-auth-test-"));
  let server;
  let context;
  t.after(async () => {
    try {
      await context?.close();
    } finally {
      try {
        if (server?.listening) {
          server.closeAllConnections();
          await new Promise((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
          );
        }
      } finally {
        await rm(profile, { recursive: true, force: true });
      }
    }
  });
  const article = structuredClone(baseArticle);
  const user = { ...baseUser };
  const mustReadsFixture = dailyFixture(article);
  const state = {
    extensionOrigin: undefined,
    active: false,
    rejectLogout: true,
    liked: false,
    feedGeneration: 1,
    feedRefreshing: false,
    bookmarked: false,
    rejectFeed: true,
    rejectNextPage: true,
    checkedWrites: 0,
    authenticatedStreams: 0,
    guestPlacementQueries: 0,
    authenticatedPlacementQueries: 0,
    interactionChecks: false,
    devCardSettings: undefined,
    profileName: "Reader Profile",
    navigationStreakDays: null,
    notificationCount: null,
    avatarCheck: undefined,
    feedSettings: { view: "cards", columns: [], content_types: ["news"], languages: ["en"] },
    onboarding: false,
    catalogScroll: false,
    stackedReader: false,
    previewRetention: false,
    previewRequests: [],
    onboardingSaved: false,
    rejectOnboardingPage: true,
    rejectTopics: true,
    rejectTopicFollow: true,
    savedTopicIds: [topicId],
    savedSourceIds: [],
    rejectSourceFollow: true,
  };
  const readingFixture = readingStreakFixture();
  const scopedViews = createFixtureFeedViews(() => state.feedSettings.view);
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
    const authenticated = state.active && headers.cookie?.includes(`${cookieName}=test-session`);
    const method = route.request().method();
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (state.previewRetention) {
      state.previewRequests.push({ url: url.href, method });
      if (
        url.pathname === "/api/v1/feed" ||
        (authenticated && ["/api/v1/user/feed", "/api/v1/user/bookmarks"].includes(url.pathname))
      )
        return send(previewRetentionFeed(article, url.searchParams));
      if (url.pathname === "/api/v1/user/engagement")
        return send(previewRetentionEngagement(url.searchParams));
      if (/^\/api\/v1\/articles\/preview-retention-\d+$/.test(url.pathname))
        return send({
          article: previewRetentionArticle(article, Number(url.pathname.split("-").at(-1))),
          topic: null,
        });
    }
    if (
      state.avatarCheck &&
      ["/api/v1/user/settings/profile", "/api/v1/user/settings/profile/avatar"].includes(
        url.pathname,
      )
    ) {
      assert.equal(authenticated, true);
      if (method !== "GET") {
        assert.equal(headers.origin, state.extensionOrigin);
        assert.equal(headers["x-csrf-token"], user.csrf_token);
        state.checkedWrites++;
        if (url.pathname === "/api/v1/user/must-reads/presentation")
          return send(await mustReadsFixture.response(true));
      }
      return state.avatarCheck(route);
    }
    if (url.pathname === "/x-ad-landing")
      return route.fulfill({
        contentType: "text/html",
        headers: {
          "Set-Cookie": `${xClickCookie}=extension-ad-click; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax; Path=/`,
        },
        body: "<p>Ad landing page</p>",
      });
    if (url.pathname === "/sso-provider-logout")
      return route.fulfill({ contentType: "text/html", body: "<p>Provider SSO session ended</p>" });
    if (url.pathname === "/login")
      return route.fulfill({
        contentType: "text/html",
        body: `<a href="/api/v1/user/auth/login?provider=github&return_to=%2Fextension%2Flogin-complete">Continue with GitHub</a>`,
      });
    if (url.pathname === "/api/v1/user/auth/config")
      return send({ enabled: true, providers: ["github"] });
    if (url.pathname === "/api/v1/user/auth/login") {
      assert.ok(
        headers.cookie?.includes(`${xClickCookie}=extension-ad-click`),
        "website signup retains the ad landing click for both extensions",
      );
      assert.equal(url.searchParams.get("return_to"), "/extension/login-complete");
      assert.equal(url.searchParams.get("provider"), "github");
      assert.equal(url.searchParams.has("register"), false);
      state.active = true;
      return route.fulfill({
        status: 302,
        headers: {
          "Set-Cookie": `${cookieName}=test-session; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax; Path=/`,
          Location: "https://devfeed.tech/extension/login-complete",
        },
      });
    }
    if (url.pathname === "/extension/login-complete")
      return route.fulfill({
        contentType: "text/html",
        body: "<!doctype html><title>Signed in</title><script>window.close()</script>",
      });
    if (url.pathname === "/api/v1/user/auth/me") return send(authenticated ? user : null);
    if (url.pathname === "/api/v1/partner-placements/query") {
      assert.equal(method, "POST");
      assert.equal(headers.cookie, undefined, "guest placements must omit the account cookie");
      assert.equal(headers["x-csrf-token"], undefined);
      state.guestPlacementQueries++;
      return send({ items: [] });
    }
    if (method !== "GET") {
      // A request already queued by the old account can arrive after revocation.
      // Match the API's 401 response; active sessions still prove cookie and CSRF delivery.
      if (!state.active) return send({}, 401);
      assert.equal(
        authenticated,
        true,
        `Chrome must send the real HttpOnly session cookie (${method} ${url.pathname}; active=${state.active}; cookie=${headers.cookie?.includes(`${cookieName}=test-session`) ?? false})`,
      );
      assert.equal(headers.origin, state.extensionOrigin);
      assert.equal(headers["x-csrf-token"], user.csrf_token);
      if (url.pathname === "/api/v1/user/partner-placements/query") {
        state.authenticatedPlacementQueries++;
        return send({ items: [] });
      }
      state.checkedWrites++;
      if (url.pathname === "/api/v1/user/must-reads/presentation")
        return send(await mustReadsFixture.response(true));
      if (url.pathname === `/api/v1/user/preferences/topics/${topicId}`) {
        const payload = route.request().postDataJSON();
        assert.deepEqual(Object.keys(payload), ["followed"]);
        if (payload.followed && state.rejectTopicFollow) {
          state.rejectTopicFollow = false;
          return send({}, 503);
        }
        state.savedTopicIds = payload.followed
          ? [...state.savedTopicIds, topicId]
          : state.savedTopicIds.filter((id) => id !== topicId);
        return send({ followed: payload.followed });
      }
      if (url.pathname.startsWith("/api/v1/user/preferences/sources/")) {
        const payload = route.request().postDataJSON();
        assert.deepEqual(Object.keys(payload), ["followed"]);
        if (payload.followed && state.rejectSourceFollow) {
          state.rejectSourceFollow = false;
          return send({}, 503);
        }
        const id = url.pathname.split("/").at(-1);
        state.savedSourceIds = payload.followed
          ? [...new Set([...state.savedSourceIds, id])]
          : state.savedSourceIds.filter((value) => value !== id);
        return send({ followed: payload.followed });
      }
      if (url.pathname === "/api/v1/user/preferences") {
        const payload = route.request().postDataJSON();
        assert.deepEqual(payload, { topic_ids: [topicId, "onboarding-0", "onboarding-1"] });
        if (state.rejectTopics) {
          state.rejectTopics = false;
          return send({}, 503);
        }
        state.savedTopicIds = payload.topic_ids;
        state.onboardingSaved = true;
        return send({ topic_ids: state.savedTopicIds });
      }
      if (url.pathname.endsWith("/auth/sso/logout")) {
        if (state.rejectLogout) {
          state.rejectLogout = false;
          return send({}, 503);
        }
        state.active = false;
        return route.fulfill({
          json: { url: "https://devfeed.tech/sso-provider-logout" },
          headers: {
            "Set-Cookie": `${cookieName}=; Max-Age=0; Secure; HttpOnly; SameSite=Lax; Path=/`,
          },
        });
      }
      if (url.pathname.endsWith("/settings/feed-view")) {
        const value = scopedViews.write(
          url.searchParams.get("feed"),
          route.request().postDataJSON(),
        );
        return send(value ?? { detail: "Invalid feed layout" }, value ? 200 : 422);
      }
      if (url.pathname.endsWith("/settings/feed")) {
        state.feedSettings = { ...state.feedSettings, ...route.request().postDataJSON() };
        return send(state.feedSettings);
      }
      if (url.pathname.endsWith("/settings/columns"))
        return send({ initialized: true, columns: state.feedSettings.columns ?? [] });
      if (url.pathname.endsWith("/settings/profile")) {
        assert.equal(route.request().postDataJSON().reading_streak, undefined);
        state.profileName = route.request().postDataJSON().display_name;
        state.devCardSettings = route.request().postDataJSON().dev_card;
        return send({
          display_name: state.profileName,
          avatar_url: null,
          dev_card: state.devCardSettings,
        });
      }
      if (url.pathname.endsWith("/like")) {
        state.liked = route.request().postDataJSON().liked;
      }
      if (url.pathname.endsWith("/bookmark") && url.pathname.includes("33333333-3333"))
        return send({ article_id: url.pathname.split("/").at(-2), bookmarked: true });
      if (url.pathname.endsWith("/bookmark")) {
        state.bookmarked = route.request().postDataJSON().bookmarked;
        return send({ article_id: article.id, bookmarked: state.bookmarked });
      }
      return send({
        article_id: article.id,
        liked: state.liked,
        likes: state.liked ? 1 : 0,
        opens: 0,
        bookmarked: state.bookmarked,
      });
    }
    if (url.pathname === "/api/v1/feed")
      return send(
        state.stackedReader
          ? stackedReaderFeed(article, url.searchParams)
          : { items: [article], next_cursor: null },
      );
    if (url.pathname === "/api/v1/feed/options")
      return send({ content_types: ["news"], sources: [], languages: ["en"] });
    if (state.catalogScroll && ["/api/v1/topics", "/api/v1/sources"].includes(url.pathname)) {
      const items = catalogChoices(url.pathname.endsWith("topics") ? "topics" : "sources").filter(
        (item) => item.name.toLowerCase().includes((url.searchParams.get("q") ?? "").toLowerCase()),
      );
      const offset = Number(url.searchParams.get("offset") ?? 0);
      return send({
        items: items.slice(offset, offset + 60),
        next_cursor: offset + 60 < items.length ? String(offset + 60) : null,
      });
    }
    if (url.pathname === "/api/v1/topics/typescript")
      return send({
        id: topicId,
        name: "TypeScript",
        slug: "typescript",
        kind: "language",
        logo_url: null,
        description: "Typed JavaScript",
        ai_description: null,
      });
    if (url.pathname === "/api/v1/sources")
      return send({ items: onboardingSources, next_cursor: null });
    if (url.pathname === "/api/v1/topics" && url.searchParams.get("q"))
      return send({
        items: onboardingTopics({
          id: topicId,
          name: "TypeScript",
          slug: "typescript",
          kind: "language",
          logo_url: null,
        }).filter((item) =>
          item.name.toLowerCase().includes(url.searchParams.get("q").toLowerCase()),
        ),
        next_cursor: null,
      });
    if (
      url.pathname === "/api/v1/topics" &&
      state.onboarding &&
      url.searchParams.get("offset") === "60"
    ) {
      if (state.rejectOnboardingPage) {
        state.rejectOnboardingPage = false;
        await new Promise((resolve) => setTimeout(resolve, 1000));
        return send({}, 503);
      }
      return send({ items: [], next_cursor: null });
    }
    if (url.pathname === "/api/v1/topics")
      return send({
        items:
          state.onboarding || state.onboardingSaved
            ? onboardingTopics({
                id: topicId,
                name: "TypeScript",
                slug: "typescript",
                kind: "language",
                logo_url: null,
                description: "Typed JavaScript",
                ai_description: null,
              })
            : [],
        next_cursor: state.onboarding ? "60" : null,
      });
    if (url.pathname.startsWith("/api/v1/articles/"))
      return send({
        article:
          signupPromptArticle(article, url.pathname.slice("/api/v1/articles/".length)) ?? article,
        topic: null,
      });
    if (url.pathname === "/api/v1/user/engagement")
      return send([
        {
          article_id: article.id,
          liked: !!authenticated && state.liked,
          likes: state.liked ? 1 : 0,
          opens: 0,
          bookmarked: !!authenticated && state.bookmarked,
        },
      ]);
    if (url.pathname === "/api/v1/user/trending")
      return send({ items: [article], next_cursor: null });
    if (!authenticated) return send({}, 401);
    if (url.pathname.startsWith("/api/v1/user/must-reads"))
      return send(await mustReadsFixture.response(url.pathname.endsWith("/presentation")));
    const endpoint = url.pathname.replace("/api/v1/user/", "");
    if (endpoint === "settings/profile")
      return send({
        display_name: state.profileName,
        dev_card: state.devCardSettings,
        avatar_url: null,
        reading_streak:
          state.navigationStreakDays === null
            ? readingFixture.profile()
            : {
                ...readingFixture.profile(),
                current_days: state.navigationStreakDays,
                longest_days: Math.max(11, state.navigationStreakDays),
                total_days: Math.max(24, state.navigationStreakDays),
              },
        stack: [],
      });
    if (endpoint === "settings/reading-week") return send(readingFixture.response());
    if (endpoint === "settings/appearance") return send({ theme: "dark" });
    if (endpoint === "settings/feed-view") {
      const value = scopedViews.read(url.searchParams.get("feed"));
      return send(value ?? { detail: "Invalid feed scope" }, value ? 200 : 422);
    }
    if (endpoint === "settings/feed") return send(state.feedSettings);
    if (endpoint === "settings/columns")
      return send({ initialized: true, columns: state.feedSettings.columns ?? [] });
    if (endpoint === "settings/notifications") return send({ show_badge: true, sound: false });
    if (endpoint === "notifications/config")
      return send({ enabled: true, environment: "users", subscriber_id: "user_a" });
    if (endpoint.endsWith("/stream")) {
      state.authenticatedStreams++;
      return route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" });
    }
    if (endpoint.endsWith("/counts"))
      return send({
        unread: state.interactionChecks ? 1 : 0,
        unseen: state.notificationCount ?? (state.interactionChecks ? 1 : 0),
      });
    if (endpoint.endsWith("/items"))
      return send(
        state.interactionChecks ? notificationFixture() : { items: [], next_cursor: null },
      );
    if (endpoint === "preferences") return send({ topic_ids: state.savedTopicIds });
    if (endpoint.endsWith("/preferences")) return send({ preferences: [] });
    if (endpoint === "preferences/sources") return send({ source_ids: state.savedSourceIds });
    if (endpoint === "feed") {
      if (state.onboarding) {
        if (state.onboardingSaved) state.onboarding = false;
        return send({
          items: [],
          next_cursor: null,
          has_interests: state.onboardingSaved,
          status: state.onboardingSaved ? "refreshing" : "ready",
          reasons: {},
        });
      }
      if (state.rejectFeed) {
        state.rejectFeed = false;
        return send({}, 503);
      }
      if (url.searchParams.has("cursor")) {
        state.rejectNextPage = false;
        return send({}, 409);
      }
      const selectedGeneration = Number(url.searchParams.get("generation") ?? state.feedGeneration);
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
        next_cursor: state.rejectNextPage ? "outdated" : null,
        status: state.feedRefreshing ? "refreshing" : "ready",
        has_interests: true,
        reasons: {},
      });
    }
    if (endpoint === "bookmarks")
      return send({ items: state.bookmarked ? [article] : [], next_cursor: null });
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
  server = createServer(
    {
      key: await readFile(path.join(profile, "key.pem")),
      cert: await readFile(path.join(profile, "cert.pem")),
    },
    async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const buffer = Buffer.concat(chunks);
      const body = buffer.toString();
      try {
        await handle({
          request: () => ({
            url: () => `https://devfeed.tech${request.url}`,
            allHeaders: async () => request.headers,
            method: () => request.method,
            postDataJSON: () => JSON.parse(body),
            postDataBuffer: () => buffer,
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
  context = await chromium.launchPersistentContext(profile, {
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? undefined
      : browser === "edge"
        ? "msedge"
        : "chromium",
    headless: true,
    hasTouch,
    viewport: { width: 1440, height: 1000 },
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
      `--host-resolver-rules=MAP devfeed.tech 127.0.0.1:${server.address().port}`,
      "--no-proxy-server",
      "--ignore-certificate-errors",
    ],
  });

  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(newTab);
  state.extensionOrigin = page.url().split("/").slice(0, 3).join("/");
  await page.waitForURL((url) => !url.hash || url.hash === "#/");
  const signIn = async () => {
    state.active = true;
    await context.addCookies([
      {
        name: cookieName,
        value: "test-session",
        domain: "devfeed.tech",
        path: "/",
        secure: true,
        httpOnly: true,
        sameSite: "Lax",
      },
    ]);
    await page.reload();
    await page.getByRole("button", { name: "User menu: RP Reader Profile", exact: true }).waitFor();
  };
  await check({
    page,
    context,
    state,
    errors,
    analytics,
    article,
    user,
    mustReadsFixture,
    readingFixture,
    signIn,
  });
  assert.deepEqual(errors, [], "browser and fixture errors");
}

test("columns reorder by dragging and keyboard without reloading feeds", { timeout: 180000 }, (t) =>
  withAuthenticatedReader(t, async ({ page, user, signIn }) => {
    await signIn();
    await checkColumnsReordering(
      page,
      readerRouteUrl(page.url(), "/"),
      `reports/columns-reordering/${browser}`,
      { csrfToken: user.csrf_token },
    );
  }),
);

test(
  "saved reader views isolate loaded workspaces across account changes",
  { timeout: 240000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, context, state, user }) => {
      await checkSavedReaderLifecycle(
        page,
        readerRouteUrl(page.url(), "/"),
        readerViewsReportDirectory(browser, "account-lifecycle"),
        {
          csrfToken: user.csrf_token,
          session: async (owner) => {
            state.active = owner !== null;
            if (owner) user.user_id = owner;
            await context.clearCookies();
            if (owner)
              await context.addCookies([
                {
                  name: cookieName,
                  value: "test-session",
                  domain: "devfeed.tech",
                  path: "/",
                  secure: true,
                  httpOnly: true,
                  sameSite: "Lax",
                },
              ]);
            else
              assert.equal(
                await page.evaluate(async (csrfToken) => {
                  const response = await fetch(
                    "https://devfeed.tech/api/v1/user/partner-placements/query",
                    {
                      method: "POST",
                      credentials: "include",
                      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
                      body: JSON.stringify({ surface: "header" }),
                    },
                  );
                  return response.status;
                }, user.csrf_token),
                401,
                "A private placement request is rejected after session revocation",
              );
          },
        },
      );
      assert.ok(
        state.authenticatedPlacementQueries > 0,
        "Member placements prove cookie and CSRF delivery",
      );
    }),
);

test(
  "saved reader views recover failed column saves without resetting feeds",
  { timeout: 240000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, user, signIn }) => {
      await signIn();
      await checkColumnSaveRecovery(
        page,
        readerRouteUrl(page.url(), "/"),
        readerViewsReportDirectory(browser, "save-recovery"),
        { csrfToken: user.csrf_token },
      );
    }),
);

for (const signedIn of [false, true])
  test(
    `saved reader views meet accessibility and motion checks for ${signedIn ? "members" : "guests"}`,
    { timeout: 360000 },
    (t) =>
      withAuthenticatedReader(t, async ({ page, signIn }) => {
        if (signedIn) await signIn();
        await checkSavedReaderAccessibility(
          page,
          readerRouteUrl(page.url(), "/"),
          readerViewsReportDirectory(browser, `accessibility-${signedIn ? "member" : "guest"}`),
          { signedIn },
        );
      }),
  );

test(
  "columns feeds load independently with bounded resources and shared actions",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn }) => {
      const target = readerRouteUrl(page.url(), "/");
      for (const signedIn of [true]) {
        if (signedIn) await signIn();
        await runReaderViewCheck(browser, "column-budgets", async (output) => {
          await checkColumnsFeeds(page, target, `${output}/observations`, { signedIn });
          return { evidence: ["observations.json"] };
        });
      }
    }),
);

test(
  "columns editorial scanning stays readable across themes and viewport sizes",
  { timeout: 90000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn }) => {
      await signIn();
      await checkColumnsScanning(
        page,
        readerRouteUrl(page.url(), "/"),
        path.join(
          await createReaderArtifactDirectory(browser, {
            parentDirectory: process.env.DEVFEED_COLUMNS_SCANNING_OUTPUT,
          }),
          `columns-scanning/${browser}-final`,
        ),
        { baseline: process.env.DEVFEED_COLUMNS_SCANNING_BASELINE === "1" },
      );
    }),
);

test(
  "columns show contextual sponsored products with signed tracking across layouts",
  { timeout: 90000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn }) => {
      await signIn();
      await checkColumnsSponsoredProducts(
        page,
        readerRouteUrl(page.url(), "/"),
        artifactPath(`columns-sponsored/${browser}`),
      );
    }),
);

test(
  "columns workspace saves independent selections and accessible arrangements",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn }) => {
      const target = readerRouteUrl(page.url(), "/?sort=most_liked");
      await checkGuestColumnsUnavailable(
        page,
        target,
        artifactPath(`columns-workspace/${browser}-guest`),
      );
      for (const signedIn of [true]) {
        if (signedIn) await signIn();
        await checkColumnsWorkspace(
          page,
          target,
          artifactPath(`columns-workspace/${browser}-${signedIn ? "member" : "guest"}`),
          { signedIn },
        );
      }
    }),
);

test(
  "columns workspace saves first-use starters without replacing user choices",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn }) => {
      const target = readerRouteUrl(page.url(), "/");
      for (const signedIn of [true]) {
        if (signedIn) await signIn();
        await checkColumnsStarters(
          page,
          target,
          artifactPath(`columns-starters/${browser}-${signedIn ? "member" : "guest"}`),
          { signedIn },
        );
      }
    }),
);

test(
  "columns workspace saves personalized My feed independently from Latest",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn, context, state, user }) => {
      const target = readerRouteUrl(page.url(), "/");
      for (const signedIn of [true]) {
        if (signedIn) await signIn();
        await checkPersonalColumns(
          page,
          target,
          artifactPath(`columns-personal/${browser}-${signedIn ? "member" : "guest"}`),
          {
            signedIn,
            identity: user,
            expireSession: async () => {
              state.active = false;
              await context.clearCookies();
            },
          },
        );
      }
    }),
);

test(
  "ordinary account resume refreshes remote preferences before a bulk save",
  { timeout: 90000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, user, signIn }) => {
      await signIn();
      await checkSessionPreferences(
        page,
        readerRouteUrl(page.url(), "/"),
        artifactPath(`session-preferences/${browser}`),
        { csrfToken: user.csrf_token },
      );
    }),
);

test(
  "article previews retain loaded feeds without refreshing session or preferences",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, state, signIn }) => {
      state.previewRetention = true;
      const current = page.url();
      for (const signedIn of [false, true]) {
        if (signedIn) await signIn();
        const routes = [
          "/latest?sort=most_liked",
          ...(signedIn ? ["/?sort=newest", "/read-later"] : []),
        ];
        for (const route of routes)
          await checkPreviewFeedRetention(
            page,
            readerRouteUrl(current, route),
            artifactPath(
              `preview-retention/${browser}-${signedIn ? "member" : "guest"}-${route.startsWith("/latest") ? "latest" : route.startsWith("/read-later") ? "saved" : "personal"}.json`,
            ),
            {
              requests: () => state.previewRequests,
              baseline: process.env.DEVFEED_PREVIEW_RETENTION_BASELINE === "1",
            },
          );
      }
    }),
);

test("tab resume retains paginated feeds and reading position", { timeout: 180000 }, (t) =>
  withAuthenticatedReader(t, async ({ page, state, signIn }) => {
    state.previewRetention = true;
    const current = page.url();
    for (const signedIn of [false, true]) {
      if (signedIn) await signIn();
      const routes = [
        "/latest?sort=most_liked",
        ...(signedIn ? ["/?sort=newest", "/read-later"] : []),
      ];
      for (const route of routes)
        await checkPreviewFeedRetention(
          page,
          readerRouteUrl(current, route),
          artifactPath(
            `tab-resume/${browser}-${signedIn ? "member" : "guest"}-${route.startsWith("/latest") ? "latest" : route.startsWith("/read-later") ? "saved" : "personal"}.json`,
          ),
          {
            resumeOnly: true,
            requests: () => state.previewRequests,
            baseline: process.env.DEVFEED_PREVIEW_RETENTION_BASELINE === "1",
          },
        );
    }
  }),
);

test(
  "stacked reader preserves presentation, preferences, and article actions",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, context, state, article, signIn }) => {
      withManagedImage(article);
      await mockManagedImages(context);
      Object.assign(article, stackedReaderArticle(article));
      state.stackedReader = true;
      const target = readerRouteUrl(page.url(), "/latest?sort=most_liked");
      for (const signedIn of [false, true]) {
        if (signedIn) await signIn();
        await checkStackedReader(
          page,
          target,
          artifactPath(`stacked-reader/${browser}-${signedIn ? "member" : "guest"}`),
          { signedIn },
        );
      }
    }),
);

// Match the web fixture: each identity owns its native-touch context. Reusing
// the extension page carries CDP/capture emulation state into the next check.
for (const signedIn of [false, true])
  test(
    `stacked reader supports native touch targets and preview activation (${signedIn ? "member" : "guest"})`,
    { timeout: 180000 },
    (t) =>
      withAuthenticatedReader(
        t,
        async ({ page, context, state, article, signIn }) => {
          withManagedImage(article);
          await mockManagedImages(context);
          Object.assign(article, stackedReaderArticle(article));
          state.stackedReader = true;
          if (signedIn) await signIn();
          await checkStackedTouchReader(
            page,
            readerRouteUrl(page.url(), "/latest?sort=most_liked"),
            artifactPath(`stacked-touch/${browser}-${signedIn ? "member" : "guest"}`),
          );
        },
        { hasTouch: true },
      ),
  );

test(
  "reader preferences persist and reconcile guest and account workspaces",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, context, state, user, signIn }) => {
      await checkReaderPreferences(page, readerRouteUrl(page.url(), "/latest"), {
        csrfToken: user.csrf_token,
        session: async (account) => {
          if (account) await signIn();
          else {
            state.active = false;
            await context.clearCookies();
            await page.reload();
          }
        },
      });
    }),
);

test(
  "feed layouts persist independently by semantic feed for guests and members",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn }) => {
      const target = readerRouteUrl(page.url(), "/");
      for (const signedIn of [false, true]) {
        if (signedIn) await signIn();
        await checkIndependentFeedViews(
          page,
          target,
          artifactPath(`feed-views/${browser}-${signedIn ? "member" : "guest"}`),
          { signedIn },
        );
      }
    }),
);

test("article layouts save in Appearance for guests and members", { timeout: 180000 }, (t) =>
  withAuthenticatedReader(t, async ({ page, signIn }) => {
    const target = readerRouteUrl(page.url(), "/latest?sort=most_liked");
    for (const signedIn of [false, true]) {
      if (signedIn) await signIn();
      await checkArticleViewSettings(
        page,
        target,
        `/tmp/devfeed-article-layout/${browser}-${signedIn ? "member" : "guest"}`,
        {
          signedIn,
          routes: [
            readerRouteUrl(target, "/trending"),
            ...(signedIn ? [readerRouteUrl(target, "/read-later")] : []),
          ],
        },
      );
    }
  }),
);

test(
  "website recognition badges are absent from the built reader extension",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page }) => {
      assert.equal(await page.locator(".sidebar-featured").count(), 0);
    }),
);

test("authenticated navigation works in the built reader extension", { timeout: 180000 }, (t) =>
  withAuthenticatedReader(t, async ({ page, state, mustReadsFixture, signIn }) => {
    const directory = path.resolve(
      import.meta.dirname,
      `../../../reports/mobile-navigation/${browser}`,
    );
    await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
    await checkReaderNavigation(page, directory);
    mustReadsFixture.enabled = true;
    mustReadsFixture.presented = true;
    await signIn();
    for (const days of [2, 123]) {
      state.navigationStreakDays = days;
      state.notificationCount = days === 123 ? 123 : 1;
      await page.reload();
      await page
        .getByRole("button", {
          name: days === 123 ? "Notifications, 99+ new (123 in total)" : "Notifications, 1 new",
          exact: true,
        })
        .waitFor();
      await checkReaderNavigation(page, directory, { signedIn: true, streakDays: days });
    }
  }),
);

test("reading streak achievements work in the built reader extension", { timeout: 180000 }, (t) =>
  withAuthenticatedReader(t, async ({ page, readingFixture, mustReadsFixture, signIn }) => {
    mustReadsFixture.presented = true;
    await signIn();
    await checkReadingStreak(
      page,
      path.resolve(import.meta.dirname, `../../../reports/reading-streak/${browser}`),
      { fixture: readingFixture, mustReads: mustReadsFixture },
    );
  }),
);

test("must reads work in the built reader extension", { timeout: 180000 }, (t) =>
  withAuthenticatedReader(t, async ({ page, mustReadsFixture, signIn }) => {
    await signIn();
    await checkMustReads(
      page,
      mustReadsFixture,
      path.resolve(extension, `../${browser}-must-reads`),
    );
  }),
);

test(
  "authentication journey signs in, performs protected writes, and signs out across tabs",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(
      t,
      async ({ page, context, state, errors, analytics, article, user }) => {
        await checkLeaderboard(page, path.resolve(extension, `../leaderboard-guest-${browser}`));
        await checkDevCardPromo(page, path.resolve(extension, "../dev-card-promo-" + browser), {
          extension: true,
        });
        await checkSignupPrompts(
          context,
          page.url(),
          path.resolve(extension, `../${browser}-signup`),
          {
            extension: true,
          },
        );
        await page.bringToFront();
        // Synchronize the clock after promo checks without freezing Date.now():
        // focus refreshes must be able to advance past the session throttle.
        await page.clock.setSystemTime(await page.evaluate(() => Date.now()));
        analytics.length = 0;
        const landing = await context.newPage();
        await landing.goto("https://devfeed.tech/x-ad-landing?twclid=extension-ad-click");
        assert.equal((await landing.evaluate(() => document.cookie)).includes(xClickCookie), false);
        await landing.close();
        const opened = context.waitForEvent("page");
        await page.getByRole("link", { name: "Save my dev card", exact: false }).click();
        const login = await opened;
        const loginClosed = login.waitForEvent("close");
        await login.getByRole("link", { name: "Continue with GitHub" }).click();
        await loginClosed;
        assert.equal(login.isClosed(), true, "the completed sign-in tab closes itself");
        await page.bringToFront();
        await page.waitForTimeout(150);
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await page
          .getByRole("button", { name: "User menu: RP Reader Profile", exact: true })
          .waitFor();
        assert.equal(
          await page.getByRole("dialog", { name: "Your dev card preview" }).count(),
          0,
          "Sign-in does not repeat a preview already shown anonymously",
        );
        assert.equal(await page.locator("html").getAttribute("class"), "dark");
        const session = (await context.cookies("https://devfeed.tech")).find(
          (cookie) => cookie.name === cookieName,
        );
        assert.ok(session.expires > Date.now() / 1000 + 29 * 86400);
        assert.ok(session.httpOnly && session.secure);
        const returningTab = await context.newPage();
        await returningTab.goto(newTab);
        await returningTab
          .getByRole("button", { name: "User menu: RP Reader Profile", exact: true })
          .waitFor();
        await checkLeaderboard(
          returningTab,
          path.resolve(extension, `../leaderboard-signed-in-${browser}`),
          { signedIn: true },
        );
        await checkUnclaimedDevCardPromo(
          returningTab,
          path.resolve(extension, "../dev-card-promo-" + browser),
          { extension: true },
        );
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
        await page
          .getByText(
            "Your dev card preview is ready. Review your details and save changes to keep it.",
          )
          .waitFor();
        assert.equal(
          await page.getByRole("textbox", { name: /Display name/ }).inputValue(),
          "Maya Chen",
        );
        await page.getByRole("textbox", { name: /Display name/ }).fill("Updated Reader");
        await page
          .getByRole("button", { name: "Save changes", exact: true })
          .locator("svg.lucide-save")
          .waitFor({ state: "visible" });
        await page.getByRole("button", { name: "Save changes", exact: true }).click();
        await page
          .getByRole("button", { name: "User menu: UR Updated Reader", exact: true })
          .waitFor();
        await page.getByRole("textbox", { name: /Display name/ }).fill("Reader Profile");
        await page.getByRole("button", { name: "Save changes", exact: true }).click();
        await page
          .getByRole("button", { name: "User menu: RP Reader Profile", exact: true })
          .waitFor();
        await checkProfileEditor(page, path.resolve(extension, "../profile-direct-" + browser));
        await checkDevCard(page, path.resolve(extension, "../dev-card-" + browser));
        await checkAvatarUploads(
          page,
          path.resolve(extension, "../avatar-" + browser),
          (handler) => {
            state.avatarCheck = handler;
          },
        );
        // Promo/profile checks deliberately jump the virtual wall clock. Analytics
        // can correctly start a new session when those jumps move time backwards.
        // Verify one shared session across the remaining chronological navigation,
        // fresh-tab, background-tab, and sign-out journeys instead.
        // Keep Date.now aligned across tabs after the virtual-clock scenarios.
        // Timers continue normally; advance the shared wall clock explicitly for
        // the focus-refresh throttle instead of relying on elapsed test runtime.
        let analyticsNow = await page.evaluate(() => Date.now());
        const syncAnalyticsTime = async (advance = 0) => {
          analyticsNow += advance;
          await Promise.all(context.pages().map((tab) => tab.clock.setFixedTime(analyticsNow)));
        };
        const chronologicalPage = async () => {
          const tab = await context.newPage();
          await tab.clock.setFixedTime(analyticsNow);
          return tab;
        };
        await syncAnalyticsTime();
        const sessionEventsStart = analytics.length;
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
          if (suffix === "appearance")
            await checkSelectMenus(page, ["Date format", "Time format", "Timezone"]);
        }
        await checkLanguagePreferences(page, page.url().split("#")[0] + "#");
        state.catalogScroll = true;
        await checkCatalogScroll(
          page,
          page.url().split("#")[0] + "#",
          path.resolve(extension, `../catalog-${browser}`),
        );
        state.catalogScroll = false;
        assert.ok(state.authenticatedStreams > 0, "notification streams carry the website session");
        state.rejectFeed = true;
        state.rejectNextPage = true;
        await page.locator(".sidebar").getByRole("link", { name: "My feed", exact: true }).click();
        await page.getByRole("region", { name: "Feed controls" }).waitFor();
        assert.equal(await page.getByRole("link", { name: /^Get for (Chrome|Edge)$/ }).count(), 0);
        assert.equal(
          await page
            .locator(".sidebar .shared-sidebar-content > nav")
            .first()
            .getByRole("link")
            .first()
            .innerText(),
          "My feed",
        );
        await page.getByRole("heading", { name: "Couldn’t load your feed" }).waitFor();
        const personalUrl = page.url();
        assert.ok(personalUrl.endsWith("#/"));
        await page.getByRole("link", { name: "Try again", exact: true }).click();
        await page.locator(".article-card").first().waitFor();
        assert.equal(await page.locator(".article-grid").count(), 1);
        assert.equal(await page.locator(".article-card").count(), 1);
        assert.equal(page.url(), personalUrl, "retry reloads the local personal feed");
        await page.locator(".pagination").scrollIntoViewIfNeeded();
        await page.getByRole("link", { name: "Show updated feed", exact: true }).click();
        await page.locator(".article-card").first().waitFor();
        assert.equal(page.url(), personalUrl, "generation recovery stays inside the extension");
        assert.equal(state.rejectNextPage, false, "the stale cursor was rejected");
        await checkArticleViews(
          page,
          personalUrl,
          path.resolve(extension, `../${browser}-personal-view`),
          [personalUrl.replace(/#\/$/, "#/latest"), personalUrl],
        );
        await checkCompactLoading(
          page,
          personalUrl,
          path.resolve(extension, `../${browser}-compact`),
          compactLoadingGate(page),
        );
        await checkAccessibility(page, browser, { signedIn: true });
        const feedRequests = [];
        page.on("request", (request) => {
          if (new URL(request.url()).pathname === "/api/v1/user/feed")
            feedRequests.push(request.url());
        });
        await checkPreviewBackground(
          page,
          path.resolve(extension, `../${browser}-personal-preview.png`),
        );
        // Finish the first cache batch, then prove identical feed responses and
        // preview lookups do not repeatedly write the entire session cache.
        await page.waitForFunction(() => {
          const cache = JSON.parse(sessionStorage.getItem("devfeed:public-reader-cache") ?? "null");
          return cache?.articles.some((item) => item.slug === "signed-in-article");
        });
        await page.evaluate(() => {
          window.__publicCacheWrites = 0;
          const setItem = Storage.prototype.setItem;
          Storage.prototype.setItem = function (key, value) {
            if (key === "devfeed:public-reader-cache") window.__publicCacheWrites++;
            return setItem.call(this, key, value);
          };
        });
        const existingCard = await page.locator(".article-card").first().elementHandle();
        // The card footer only exposes likes when the card has enough room.
        await page.setViewportSize({ width: 520, height: 1000 });
        await page.getByRole("button", { name: /^Like article/ }).click();
        await page.getByRole("button", { name: /^Unlike article/ }).waitFor();
        await page.setViewportSize({ width: 1440, height: 1000 });
        assert.equal(feedRequests.length, 0);
        assert.equal(await existingCard.evaluate((node) => node.isConnected), true);
        assert.equal(
          await page.getByText("Updating recommendations in the background…").count(),
          0,
        );
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
        await syncAnalyticsTime(61_000);
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
        await checkPreviewBackground(page);
        await page.waitForTimeout(1300);
        assert.equal(
          await page.evaluate(() => window.__publicCacheWrites),
          0,
          "Identical feed records and cached previews must not rewrite session storage",
        );
        await page.screenshot({ path: path.resolve(extension, `../${browser}-feed-refresh.png`) });
        state.feedGeneration = 2;
        state.feedRefreshing = false;
        assert.equal(await page.getByRole("link", { name: article.title, exact: true }).count(), 1);
        state.feedGeneration = 3;
        const pinnedResponse = page.waitForResponse((response) => {
          const url = new URL(response.url());
          return url.pathname === "/api/v1/user/feed" && url.searchParams.get("generation") === "1";
        });
        await page.evaluate(() => window.dispatchEvent(new Event("devfeed:extension-refresh")));
        await pinnedResponse;
        await page.getByRole("link", { name: article.title, exact: true }).waitFor();
        const freshTab = await chronologicalPage();
        await freshTab.goto(personalUrl);
        await freshTab.getByRole("link", { name: "Hourly recommendation", exact: true }).waitFor();
        await freshTab.close();
        state.feedGeneration = 2;
        await page.getByRole("button", { name: "Save article for later", exact: true }).click();
        await page.getByRole("button", { name: "Remove bookmark", exact: true }).waitFor();
        await page
          .locator(".sidebar")
          .getByRole("link", { name: "Read later", exact: true })
          .click();
        await checkArticleViews(
          page,
          page.url(),
          path.resolve(extension, `../${browser}-saved-view`),
        );
        await checkPreviewBackground(
          page,
          path.resolve(extension, `../${browser}-bookmarks-preview.png`),
        );
        await page.locator(".card-open-link").first().click();
        await page.locator("#article-preview-title").waitFor();
        const modalUrl = page.url();
        await page.bringToFront();
        await page.waitForTimeout(150);
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await page.locator("#article-preview-title").waitFor();
        assert.equal(page.url(), modalUrl);
        await page.reload();
        await page.locator("#article-preview-title").waitFor();
        await page.getByRole("button", { name: "Close preview", exact: true }).click();
        await page.locator("dialog").waitFor({ state: "detached" });

        state.onboarding = true;
        state.savedTopicIds = [];
        // Start a fresh reader after changing the server-side fixture preferences.
        await page.reload();
        await checkFeedOnboarding(
          page,
          personalUrl,
          path.resolve(extension, `../${browser}-onboarding`),
        );
        assert.ok(state.onboardingSaved);
        assert.deepEqual(state.savedTopicIds, [topicId, "onboarding-0", "onboarding-1"]);
        article.topics = [
          { id: topicId, name: "TypeScript", slug: "typescript", kind: "language" },
        ];
        const localBase = personalUrl.split("#")[0];
        await checkTopicFollow(
          page,
          `${localBase}#/topics/typescript`,
          `${localBase}#/topics/typescript/news?language=en`,
          path.resolve(extension, `../${browser}-onboarding`),
        );
        await checkSourceFollow(
          page,
          `${localBase}#/sources`,
          onboardingSources,
          path.resolve(extension, `../${browser}-onboarding`),
        );

        state.interactionChecks = true;
        const interactions = await chronologicalPage();
        try {
          await checkReaderInteractions(
            interactions,
            (route) => `${localBase}#${route}`,
            path.resolve(extension, `../${browser}-mobile-interactions.png`),
          );
        } finally {
          await interactions.close();
          state.interactionChecks = false;
        }
        await page.bringToFront();

        const second = await chronologicalPage();
        // A new tab can leave focus in the omnibox. Keep that state throughout
        // startup, and also load it behind the existing tab without interacting.
        await second.addInitScript(() => {
          Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false });
        });
        await page.bringToFront();
        await second.goto(newTab);
        await second
          .getByRole("button", { name: "User menu: RP Reader Profile", exact: true })
          .waitFor();
        await second.getByRole("region", { name: "Feed controls" }).waitFor();
        assert.equal(new URL(second.url()).hash.replace(/^#/, "") || "/", "/");
        await second.getByRole("link", { name: "Updated recommendation", exact: true }).waitFor();
        assert.equal(await second.evaluate(() => document.hasFocus()), false);
        await second.screenshot({
          path: path.resolve(extension, `../${browser}-unfocused-feed.png`),
        });
        await page.bringToFront();
        await checkFeedSort(page, page.url().split("#")[0] + "#", true);
        const preparationPage = await chronologicalPage();
        await checkFeedPreparation(
          preparationPage,
          personalUrl,
          article,
          path.resolve(import.meta.dirname, `../../../reports/feed-preparation/${browser}`),
        );
        await preparationPage.close();
        await page.bringToFront();
        await page
          .getByRole("button", { name: "User menu: RP Reader Profile", exact: true })
          .click();
        await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
        await page.getByText("Couldn’t sign out. Please try again.").waitFor();
        assert.equal(await page.locator(".user-menu-trigger").count(), 1);
        await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
        await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
        await second.getByRole("link", { name: "Sign in", exact: true }).waitFor();
        await page.waitForURL((url) => !url.hash || url.hash === "#/");
        await second.waitForURL((url) => !url.hash || url.hash === "#/");
        assert.equal(await page.getByRole("link", { name: "Read later", exact: true }).count(), 0);
        assert.equal(
          await second.getByRole("link", { name: "Read later", exact: true }).count(),
          0,
        );
        assert.ok(page.url().startsWith("chrome-extension://"));
        assert.ok(state.checkedWrites >= 4);
        assert.ok(state.guestPlacementQueries > 0, "guest placement transport was exercised");
        assert.ok(
          state.authenticatedPlacementQueries > 0,
          "authenticated placement transport was exercised",
        );
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
        assert.equal(
          new Set(analytics.slice(sessionEventsStart).map((value) => value.session_id)).size,
          1,
          JSON.stringify(
            analytics
              .slice(sessionEventsStart)
              .map(({ event, session_id }) => ({ event, session_id })),
          ),
        );
        assert.ok(analytics.some((value) => value.engagement_time_msec > 0));
        assert.equal(JSON.stringify(analytics).includes(user.email), false);
        assert.equal(JSON.stringify(analytics).includes(user.csrf_token), false);
        const stored = await page.evaluate(() =>
          JSON.stringify({ ...localStorage, ...sessionStorage }),
        );
        assert.equal(stored.includes(user.csrf_token), false);
        assert.equal(stored.includes("test-session"), false);
        assert.deepEqual(errors, []);
      },
    ),
);

test(
  "Sign out ends local and provider SSO sessions in the built extension",
  { timeout: 180000 },
  (t) =>
    withAuthenticatedReader(t, async ({ page, signIn, state }) => {
      await signIn();
      const second = await page.context().newPage();
      await second.goto(newTab);
      await second.getByRole("button", { name: /^User menu:/ }).waitFor();
      await page.bringToFront();
      state.rejectLogout = false;
      await page.getByRole("button", { name: /^User menu:/ }).click();
      await page.getByRole("menu").waitFor();
      assert.equal(await page.getByRole("menuitem", { name: "Sign out of SSO…" }).count(), 0);
      assert.equal(await page.getByText(/Also ends your SSO session/).count(), 0);
      const opened = page.context().waitForEvent("page");
      await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
      const provider = await opened;
      await provider.waitForURL("https://devfeed.tech/sso-provider-logout");
      assert.equal(await provider.evaluate(() => window.opener === null), true);
      await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
      await second.getByRole("link", { name: "Sign in", exact: true }).waitFor();
      assert.equal(state.active, false);
      await second.close();
      await provider.close();
    }),
);
