import { tagsFixture, checkTags } from "../../../scripts/testing/tags.mjs";
import { checkCatalogSharing } from "../../../scripts/testing/catalog-sharing.mjs";
import {
  checkKeyboardReader,
  keyboardProfile,
  keyboardActivity,
} from "../../../scripts/testing/keyboard-reader.mjs";
import { checkRouteStyles } from "../../../scripts/testing/route-styles.mjs";
import { readerDomCatalogFixture, checkReaderDom } from "../../../scripts/testing/reader-dom.mjs";
import { checkGuestDocument } from "../../../scripts/testing/document-latency.mjs";
import {
  withManagedSourceLogo,
  mockSourceLogos,
  checkSourceLogos,
  checkSourceLogoPages,
} from "../../../scripts/testing/source-logos.mjs";

import {
  articleTopicContextFixture,
  checkArticleTopicContext,
} from "../../../scripts/testing/article-topic-context.mjs";

import { checkPreviewCover } from "../../../scripts/testing/preview-cover.mjs";
import { checkArticleGrid } from "../../../scripts/testing/article-grid.mjs";
import { checkDefaultAll, defaultAllFeed } from "../../../scripts/testing/default-all.mjs";
import { checkArticleViews, compactListArticle } from "../../web/tests/browser/article-views.mjs";
import { checkSourceFilter, longFilterSource } from "../../../scripts/testing/select-menus.mjs";
import { checkAccessibility } from "../../web/tests/browser/accessibility.mjs";
import {
  avatarFixtureVariants,
  avatarFixtureImage,
  checkPublicAvatar,
} from "../../../scripts/testing/avatar-uploads.mjs";
import { checkMcp, testMcpEndpoint } from "../../../scripts/testing/mcp.mjs";
import { checkSidebarGitHub } from "../../../scripts/testing/sidebar-github.mjs";
import { checkCollapsibleSidebar } from "../../../scripts/testing/collapsible-sidebar.mjs";
import { checkBrowserTheme } from "../../../scripts/testing/browser-theme.mjs";
import {
  checkUserMutations,
  userMutationFixture,
} from "../../../scripts/testing/user-mutations.mjs";
import { checkReaderRoutes, readerRoutesFixture } from "../../../scripts/testing/reader-routes.mjs";
import { checkFeedMode, feedModeFixture } from "../../../scripts/testing/feed-mode.mjs";
import {
  checkReaderContext,
  readerContextFixture,
} from "../../../scripts/testing/reader-context.mjs";
import {
  checkReaderDestinations,
  readerDestinationsFixture,
} from "../../../scripts/testing/reader-destinations.mjs";
import {
  engagementFeed,
  engagementRows,
  checkEngagementPagination,
} from "../../web/tests/browser/engagement-pagination.mjs";
import { checkPreviewBackground } from "../../../scripts/testing/preview-background.mjs";
import { checkExtensionRouteAnalytics } from "../../../scripts/testing/route-analytics.mjs";
import { blockedFeed, checkArticleFirst } from "../../web/tests/browser/article-first.mjs";
import {
  searchFixture,
  checkSearchFilters,
  checkSearchInfiniteScroll,
  checkSearchSecurity,
  searchSecurityQuery,
} from "../../../scripts/testing/search-filters.mjs";
import {
  withManagedImage,
  mockManagedImages,
  checkManagedImages,
} from "../../../scripts/testing/managed-images.mjs";
import { signInResponse, checkGuestTopicSignIn } from "../../../scripts/testing/sign-in.mjs";
import {
  checkLoginDestinations,
  loginDestinationsFixture,
} from "../../../scripts/testing/login-destinations.mjs";
import { checkServerReads, serverReadFixture } from "../../../scripts/testing/server-reads.mjs";
import assert from "node:assert/strict";
import {
  thumbnailProfiles,
  checkThumbnailSizing,
} from "../../../scripts/testing/thumbnail-sizing.mjs";
import {
  snippetArticle,
  checkArticleSnippets,
} from "../../../scripts/testing/article-snippets.mjs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { prepareSearchDraft, checkSearchDraft } from "../../../scripts/testing/search-draft.mjs";
import { fixtureImageDiagnostics } from "./fixture-image-diagnostics.mjs";

const browser = process.env.DEVFEED_EXTENSION_BROWSER ?? "chrome";
const extension = path.resolve(import.meta.dirname, `../dist/${browser}`);
const newTab = browser === "edge" ? "edge://newtab" : "chrome://newtab";
const article = {
  id: "11111111-1111-4111-8111-111111111111",
  slug: "reader-parity",
  canonical_url: "https://publisher.test/article",
  title: "Shared reader components in every new tab",
  summary: "A fixture for the reader. ".repeat(100),
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
  topics: [{ id: "topic", name: "JavaScript", slug: "javascript", kind: "technology" }],
  sources: [{ id: "source", slug: "publisher", name: "Publisher", logo_url: null }],
};

withManagedImage(article);
withManagedSourceLogo(article.sources[0]);
const filterSource = { ...article.sources[0], id: "99999999-9999-4999-8999-999999999999" };

// Each named test gets a real unpacked extension with isolated storage and mocks.
async function readerFixture(t, imageProfile) {
  const profile = await mkdtemp(path.join(tmpdir(), "devfeed-reader-test-"));
  const errors = [];
  let context;
  let imageDiagnostics;
  t.after(async () => {
    try {
      if (errors.length) {
        const filename = `${t.name.replace(/[^a-z0-9]+/gi, "-")}.json`;
        await imageDiagnostics?.writeFailure(
          path.resolve(
            import.meta.dirname,
            `../../../reports/reader-diagnostics/${browser}`,
            filename,
          ),
        );
      }
      await context?.close();
      assert.deepEqual(errors, []);
    } finally {
      await rm(profile, { recursive: true, force: true });
    }
  });
  context = await chromium.launchPersistentContext(profile, {
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? undefined
      : browser === "edge"
        ? "msedge"
        : "chromium",
    headless: true,
    viewport: imageProfile
      ? { width: imageProfile.width, height: imageProfile.height }
      : { width: 1440, height: 1000 },
    deviceScaleFactor: imageProfile?.dpr ?? 1,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  context.on("page", (page) => {
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && /Content Security Policy|Refused to/.test(message.text()))
        errors.push({ message: message.text(), location: message.location() });
    });
  });
  imageDiagnostics = await fixtureImageDiagnostics(context);
  const state = {
    engagementPagination: false,
    defaultAll: false,
    compactArticles: false,
    failNextPage: true,
    failArticle: true,
    feedOptionsStatus: 200,
    articleFeedGate: undefined,
    listingGate: undefined,
  };
  await mockManagedImages(context);
  await mockSourceLogos(context);
  await context.route("https://images.example.test/avatars/**", async (route) => {
    const width = Number(new URL(route.request().url()).pathname.split("/").at(-1).split(".")[0]);
    return route.fulfill({ contentType: "image/webp", body: await avatarFixtureImage(width) });
  });
  const requests = [];
  const analytics = [];
  const feedItems = Array.from({ length: 24 }, (_, index) => ({
    ...article,
    id: `11111111-1111-4111-8111-${String(index).padStart(12, "0")}`,
    slug: `${article.slug}-${index}`,
    image_url: article.image_url.replace("/fixture/", `/fixture-${index}/`),
    image_variants: article.image_variants.map((variant) => ({
      ...variant,
      url: variant.url.replace("/fixture/", `/fixture-${index}/`),
    })),
  }));
  const mutationFixture = userMutationFixture({
    articles: feedItems,
    topic: { ...article.topics[0], id: "ABCDEF00-0000-4000-8000-000000000001" },
  });
  const domCatalog = readerDomCatalogFixture();
  const routeFixture = readerRoutesFixture(article);
  const feedMode = feedModeFixture(article);
  const readerContext = readerContextFixture(article);
  const readerDestinations = readerDestinationsFixture(article);
  const loginDestinations = loginDestinationsFixture(article);
  const serverReads = serverReadFixture();
  const topicContext = articleTopicContextFixture(article);
  await context.route("https://identity.example/authorize?**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Sign-in provider</p>" }),
  );
  await context.route("https://devfeed.tech/login?**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<a href="/api/v1/user/auth/login?return_to=%2Fextension%2Flogin-complete">Continue to sign in</a>`,
    }),
  );
  await context.route("https://devfeed.tech/api/**", async (route) => {
    const url = new URL(route.request().url());
    serverReads.record(url.href, route.request().headers(), url.origin);
    if (topicContext.handles(url)) return route.fulfill(topicContext.respond(url));
    if (loginDestinations.handles(url)) return route.fulfill(loginDestinations.respond(url));
    if (readerDestinations.handles(url))
      return route.fulfill(
        await readerDestinations.respond(
          url,
          route.request().postData() ? route.request().postDataJSON() : undefined,
        ),
      );
    if (readerContext.handles(url))
      return route.fulfill(
        await readerContext.respond(
          url,
          route.request().postData() ? route.request().postDataJSON() : undefined,
        ),
      );
    if (feedMode.handles(url))
      return route.fulfill(
        feedMode.respond(
          url,
          route.request().postData() ? route.request().postDataJSON() : undefined,
        ),
      );
    if (domCatalog.handles(url)) return route.fulfill({ json: domCatalog.respond(url) });
    routeFixture.record(url);
    if (routeFixture.handles(url)) return route.fulfill(routeFixture.respond(url));
    const mutationPath = url.pathname.slice("/api".length);
    if (mutationFixture.handles(mutationPath, route.request().method())) {
      if (route.request().method() === "PUT")
        assert.equal(route.request().headers()["x-csrf-token"], "test");
      return route.fulfill(
        mutationFixture.respond(
          mutationPath,
          route.request().method(),
          route.request().postData() ? route.request().postDataJSON() : undefined,
        ),
      );
    }
    if (
      state.listingGate &&
      ["/api/v1/feed", "/api/v1/feed/options", "/api/v1/topics", "/api/v1/sources"].includes(
        url.pathname,
      )
    )
      await state.listingGate;
    const pendingFeed = state.articleFeedGate;
    if (pendingFeed && (await pendingFeed.wait(url.pathname)) && pendingFeed.fail) {
      return route.fulfill({ status: 503, json: {} });
    }
    requests.push(url);
    if (
      state.engagementPagination &&
      ["/api/v1/feed", "/api/v1/user/engagement"].includes(url.pathname)
    ) {
      return route.fulfill({
        json:
          url.pathname === "/api/v1/feed"
            ? engagementFeed(article, url.searchParams)
            : engagementRows(url.searchParams),
      });
    }
    if (["/api/v1/feed", "/api/v1/topics", "/api/v1/sources"].includes(url.pathname))
      assert.equal(route.request().headers()["cache-control"], "max-age=600");
    if (url.pathname === "/api/v1/articles/retry-article") {
      return state.failArticle
        ? route.fulfill({ status: 503, json: {} })
        : route.fulfill({
            json: { article: { ...article, slug: "retry-article" }, topic: null },
          });
    }
    if (url.pathname === "/api/v1/users/keyboard-reader")
      return route.fulfill({ json: { profile: keyboardProfile, activity: keyboardActivity } });
    if (url.pathname === "/api/v1/users/reader")
      return route.fulfill({
        json: {
          profile: {
            username: "reader",
            display_name: "Reader Profile",
            avatar_url: avatarFixtureVariants()[3].url,
            avatar_variants: avatarFixtureVariants(),
            bio: "A public profile in the extension.",
            links: [],
            stack: [],
            reading_streak: null,
          },
          activity: {
            year: 2026,
            timezone: "UTC",
            days: [
              { date: "2026-01-01", article_count: 3 },
              { date: "2026-01-02", article_count: 0 },
            ],
          },
        },
      });
    if (url.pathname === "/api/v1/mcp/config") {
      assert.equal(route.request().headers()["cache-control"], "no-store");
      return route.fulfill({
        json: { url: testMcpEndpoint },
      });
    }
    if (url.pathname === "/api/v1/leaderboard")
      return route.fulfill({ json: { longest_streak: [], reading_days: [] } });
    if (url.pathname === "/api/v1/extension/analytics") {
      if (route.request().method() === "POST") {
        analytics.push(route.request().postDataJSON());
        return route.fulfill({ status: 204 });
      }
      return route.fulfill({ json: { enabled: true } });
    }
    if (url.pathname === "/api/v1/user/auth/config")
      return route.fulfill({ json: { enabled: true, providers: [] } });
    if (url.pathname === "/api/v1/user/auth/login")
      return route.fulfill(await signInResponse(url.pathname.slice(4) + url.search));
    const tagResponse = tagsFixture(url);
    if (tagResponse) return route.fulfill(tagResponse);
    let json;
    if (url.pathname === "/api/v1/user/trending") {
      json = { items: feedItems, next_cursor: null };
    } else if (url.pathname === "/api/v1/feed") {
      if (state.defaultAll)
        return route.fulfill({ json: defaultAllFeed(article, url.searchParams) });
      if (
        url.searchParams.has("cursor") &&
        url.searchParams.get("sort") === "most_liked" &&
        state.failNextPage
      ) {
        state.failNextPage = false;
        return route.fulfill({ status: 503, json: {} });
      }
      const sorted = url.searchParams.get("sort") === "most_liked";
      json = url.searchParams.has("cursor")
        ? {
            items: [{ ...article, id: "next", slug: "next", title: "Next page article" }],
            next_cursor: null,
          }
        : { items: feedItems, next_cursor: sorted ? "next+page" : null };
    } else if (["/api/v1/articles/old", "/api/v1/articles/new"].includes(url.pathname)) {
      json = { article: { ...article, slug: url.pathname.split("/").pop() }, topic: null };
    } else if (url.pathname === "/api/v1/articles/snippet-article") {
      json = { article: snippetArticle(article), topic: null };
    } else if (url.pathname === "/api/v1/articles/direct-article") {
      json = { article: { ...feedItems[0], slug: "direct-article" }, topic: null };
    } else if (url.pathname === "/api/v1/user/auth/me") {
      json = null;
    } else if (url.pathname === "/api/v1/feed/options") {
      if (state.feedOptionsStatus !== 200)
        return route.fulfill({ status: state.feedOptionsStatus, json: {} });
      json = {
        sources: [filterSource, longFilterSource],
        content_types: ["article", "news", "tutorial", "release", "comparison", "opinion"],
        languages: ["en", "fr"],
      };
    } else if (url.pathname === "/api/v1/topics") {
      json = {
        items: article.topics.map((topic) => ({
          ...topic,
          logo_url: null,
          description: "Complete topic description for the article preview.",
          ai_description: null,
        })),
        next_cursor: null,
      };
    } else if (url.pathname === "/api/v1/topics/javascript") {
      json = {
        ...article.topics[0],
        description: "Complete topic description for the article preview.",
        logo_url: null,
        ai_description: null,
      };
    } else if (url.pathname === "/api/v1/sources/publisher") {
      json = article.sources[0];
    } else if (url.pathname === "/api/v1/sources") {
      json = { items: article.sources, next_cursor: null };
    } else if (url.pathname === "/api/v1/user/engagement") {
      json = feedItems.map((item) => ({ article_id: item.id, likes: 2, opens: 5, liked: false }));
    } else if (
      url.pathname === "/api/v1/search" &&
      ["microservice", "infinite-scroll", searchSecurityQuery].includes(url.searchParams.get("q"))
    ) {
      if (url.searchParams.has("sort")) await new Promise((resolve) => setTimeout(resolve, 800));
      json = searchFixture(
        url.searchParams.get("q"),
        url.searchParams.get("sort"),
        url.searchParams.get("content_types"),
        url.searchParams.get("section"),
      );
    } else if (url.pathname === "/api/v1/search") {
      json = {
        query: url.searchParams.get("q"),
        sections: {
          articles: {
            items: [
              {
                id: "hit",
                title: "Search result",
                description: "Matched article",
                href: "/articles/result",
                image_url: null,
                label: "News",
                published_at: article.published_at,
              },
            ],
            next_cursor: null,
          },
        },
      };
    } else return route.fulfill({ status: 404, json: {} });
    if (state.compactArticles && ["/api/v1/feed", "/api/v1/user/trending"].includes(url.pathname))
      json = { ...json, items: json.items.map(compactListArticle) };
    return route.fulfill({ json });
  });
  const page = await context.newPage();
  await page.goto(newTab);
  await page.locator(".article-card").first().waitFor();
  const readerBase = page.url().split("#")[0] + "#";
  return {
    context,
    page,
    requests,
    analytics,
    state,
    mutationFixture,
    routeFixture,
    feedMode,
    readerContext,
    readerDestinations,
    loginDestinations,
    serverReads,
    domCatalog,
    topicContext,
    pathFor: (path) => readerBase + path,
  };
}

test("source and topic sharing uses public collection links", { timeout: 180000 }, async (t) => {
  const { page, pathFor } = await readerFixture(t);
  await checkCatalogSharing(
    page,
    pathFor,
    [
      { kind: "source", path: "/sources/publisher" },
      { kind: "topic", path: "/topics/javascript" },
    ],
    `/tmp/devfeed-catalog-sharing/${browser}`,
    "https://devfeed.tech",
  );
});

test(
  "keyboard access preserves native links and readable scroll regions",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor } = await readerFixture(t);
    await checkKeyboardReader(page, pathFor, `/tmp/devfeed-sonar-a11y/${browser}`);
  },
);

test(
  "article topic panels preserve contextual assignments in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, topicContext } = await readerFixture(t);
    await checkArticleTopicContext(
      page,
      pathFor,
      topicContext,
      `${process.env.DEVFEED_TOPIC_CONTEXT_REPORT_DIR ?? path.resolve(import.meta.dirname, "../../../reports/article-topic-context")}/${browser}`,
    );
  },
);

test("article snippets exclude related copy in built new tabs", { timeout: 180000 }, async (t) => {
  const { page, pathFor } = await readerFixture(t);
  await checkArticleSnippets(page, pathFor("/articles/snippet-article"));
});

test(
  "thumbnails match rendered widths and DPR in built new tabs",
  { timeout: 360000 },
  async (t) => {
    const results = [];
    for (const profile of thumbnailProfiles) {
      for (const sidebar of ["collapsed", "expanded"]) {
        const { context, page, pathFor } = await readerFixture(t, profile);
        results.push(...(await checkThumbnailSizing(page, pathFor("/latest"), sidebar)));
        await context.close();
      }
    }
    const { mkdir, writeFile } = await import("node:fs/promises");
    const output = path.resolve(import.meta.dirname, "../../../reports/thumbnail-sizing");
    await mkdir(output, { recursive: true });
    await writeFile(`${output}/${browser}.json`, JSON.stringify(results, null, 2));
  },
);

test(
  "server reads retain explicit transport policy in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, serverReads } = await readerFixture(t);
    await checkServerReads(
      page,
      pathFor,
      serverReads,
      path.resolve(import.meta.dirname, `../../../reports/server-reads/${browser}`),
      { publicOrigin: "https://devfeed.tech", userOrigin: "https://devfeed.tech", extension: true },
    );
  },
);

test("login destinations work inside built new tabs", { timeout: 180000 }, async (t) => {
  const { page, pathFor, loginDestinations } = await readerFixture(t);
  await checkLoginDestinations(
    page,
    pathFor,
    loginDestinations,
    path.resolve(import.meta.dirname, `../../../reports/login-destinations/${browser}`),
    { extension: true },
  );
});

test(
  "telemetry labels protect private routes in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, analytics } = await readerFixture(t);
    await checkExtensionRouteAnalytics(page, pathFor, analytics);
    assert.ok(analytics.every((event) => event.client_platform === `${browser}_extension`));
  },
);

test(
  "reader navigation preserves article overlays and inbox destinations in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, readerDestinations } = await readerFixture(t);
    await checkReaderDestinations(
      page,
      pathFor,
      readerDestinations,
      path.resolve(import.meta.dirname, `../../../reports/reader-destinations/${browser}`),
      true,
    );
  },
);

test(
  "reader context controls article prompts in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, readerContext } = await readerFixture(t);
    await checkReaderContext(
      page,
      pathFor,
      readerContext,
      path.resolve(import.meta.dirname, `../../../reports/reader-context/${browser}`),
      true,
    );
  },
);

test(
  "feed modes expose placement capabilities in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, feedMode } = await readerFixture(t);
    await checkFeedMode(
      page,
      pathFor,
      feedMode,
      path.resolve(import.meta.dirname, `../../../reports/feed-mode/${browser}`),
      true,
    );
  },
);

test(
  "reader routes validate missing pages and recovery in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, routeFixture } = await readerFixture(t);
    await checkReaderRoutes(
      page,
      pathFor,
      routeFixture,
      path.resolve(import.meta.dirname, `../../../reports/reader-routes/${browser}`),
      true,
    );
  },
);

test("user mutations apply semantic effects in built new tabs", { timeout: 180000 }, async (t) => {
  const { context, pathFor, mutationFixture } = await readerFixture(t);
  const page = await context.newPage();
  await checkUserMutations(page, pathFor, mutationFixture);
});

test(
  "reader theme persists and follows system settings in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page } = await readerFixture(t);
    await checkBrowserTheme(page, path.resolve(extension, `../${browser}-shared-theme`));
  },
);

test("reader sidebar persists and adapts in built new tabs", { timeout: 180000 }, async (t) => {
  const { page } = await readerFixture(t);
  await checkCollapsibleSidebar(page, path.resolve(extension, `../${browser}-shared-sidebar`));
});

test(
  "reader journey works in built new tabs at desktop and mobile sizes",
  { timeout: 180000 },
  async (t) => {
    const { context, page, requests, analytics, state } = await readerFixture(t);
    const readerBase = `${page.url().split("#")[0]}#`;
    state.defaultAll = true;
    await page.goto("about:blank");
    await checkDefaultAll(
      page,
      readerBase,
      path.resolve(import.meta.dirname, `../../../reports/default-all/${browser}`),
      () => requests,
    );
    state.defaultAll = false;
    await page.goto(newTab);
    await page.locator(".article-card").first().waitFor();
    await checkManagedImages(page);
    await checkSourceLogos(page);
    await checkSourceLogoPages(page);

    await checkPreviewCover(
      context,
      `${page.url().split("#")[0]}#/articles/direct-article`,
      path.resolve(import.meta.dirname, `../../../reports/preview-cover/${browser}`),
    );
    await checkArticleGrid(page, path.join(extension, "../grid-" + browser));
    const base = page.url().split("#")[0];
    await checkArticleViews(
      page,
      `${base}#/latest`,
      path.join(extension, `../${browser}-article-view`),
      [`${base}#/topics/javascript`, `${base}#/sources/publisher`, `${base}#/trending`],
      {
        fixture: (active) => {
          state.compactArticles = active;
        },
      },
    );
    await page.goto(`${base}#/latest`);
    await page.locator(".article-card").first().waitFor();
    assert.ok(page.url().startsWith("chrome-extension://"));
    await page.waitForURL(/#\/latest$/);
    for (const [path, label, ready] of [
      ["latest", "Loading articles…", ".article-card"],
      ["sources", "Loading sources…", ".topic-grid"],
      ["topics", "Loading topics…", ".topic-grid"],
    ]) {
      let release;
      state.listingGate = new Promise((resolve) => {
        release = resolve;
      });
      try {
        await page.goto(`${base}#/${path}`);
        await page.reload();
        await page.getByRole("status", { name: label, exact: true }).waitFor();
        assert.equal(await page.locator(ready).count(), 0);
        await prepareSearchDraft(page);
      } finally {
        state.listingGate = undefined;
        release();
      }
      await page.locator(ready).first().waitFor();
      await checkSearchDraft(page);
    }
    await page.goto(`${base}#/latest`);
    await page.reload();
    await page.locator(".article-card").first().waitFor();
    await checkAccessibility(page, browser);
    await checkSourceFilter(
      page,
      `${base}#/latest`,
      filterSource,
      path.join(extension, `../${browser}-filter`),
    );
    for (const status of [404, 503]) {
      state.feedOptionsStatus = status;
      const sourceRequests = requests.filter((url) => url.pathname === "/api/v1/sources").length;
      const optionsResponse = page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/api/v1/feed/options",
      );
      await page.reload();
      assert.equal((await optionsResponse).status(), status);
      await page.locator(".article-card").first().waitFor();
      assert.deepEqual(await page.locator(".feed-toolbar a").allTextContents(), ["All"]);
      assert.equal(
        requests.filter((url) => url.pathname === "/api/v1/sources").length,
        sourceRequests,
        "Unavailable feed options must not trigger a broad source-catalog fallback",
      );
    }
    state.feedOptionsStatus = 200;
    await page.reload();
    await page.locator(".feed-toolbar").getByRole("link", { name: "News", exact: true }).waitFor();
    await page.locator(".article-card").first().waitFor();
    const publicProfile = await context.newPage();
    await publicProfile.goto(page.url().split("#")[0] + "#/users/reader");
    await publicProfile.getByRole("heading", { name: "Reader Profile" }).waitFor();
    await checkPublicAvatar(publicProfile);
    assert.equal(await publicProfile.getByText("A public profile in the extension.").count(), 1);
    assert.equal(
      await publicProfile.locator(".public-profile-calendar-heading").textContent(),
      "2026",
    );
    assert.equal(await publicProfile.getByLabel("2026-01-01: 3 article opens").count(), 1);
    assert.ok(requests.some((url) => url.pathname === "/api/v1/users/reader"));
    await publicProfile.close();
    await page.bringToFront();
    for (let attempt = 0; !analytics.length && attempt < 50; attempt++) {
      await page.waitForTimeout(100);
    }
    assert.ok(analytics.length, "The built extension emits analytics");
    assert.ok(analytics.every((event) => event.client_platform === `${browser}_extension`));
    assert.ok(
      requests.some(
        (url) =>
          url.pathname === "/api/v1/feed" && url.searchParams.get("content_type") === "article",
      ),
    );
    assert.equal(await page.getByRole("link", { name: /^Get for (Chrome|Edge)$/ }).count(), 0);
    assert.equal(
      await page.getByRole("dialog", { name: "A fresh feed in every new tab." }).count(),
      0,
    );
    assert.equal(await page.getByRole("link", { name: "Read later", exact: true }).count(), 0);
    const tagged = await context.newPage();
    const campaign = "utm_source=linkedin&utm_medium=organic&utm_campaign=reader_updates";
    await tagged.goto(
      page.url().split("#")[0] + `#/?${campaign}&unrelated=discard&q=engineering&sort=oldest`,
    );
    await tagged.locator(".article-card").first().waitFor();
    assert.equal(
      new URL(tagged.url()).hash,
      `#/?${campaign}&unrelated=discard&q=engineering&sort=oldest`,
    );
    assert.ok(
      requests.some(
        (url) =>
          url.pathname === "/api/v1/feed" &&
          url.searchParams.get("q") === "engineering" &&
          url.searchParams.get("sort") === "oldest",
      ),
    );
    await tagged.close();
    const retryPage = await context.newPage();
    await retryPage.goto(page.url().split("#")[0] + "#/articles/retry-article");
    await retryPage.getByRole("heading", { name: "Couldn’t load the article" }).waitFor();
    state.failArticle = false;
    await retryPage.getByRole("button", { name: "Try again", exact: true }).click();
    await retryPage.locator("#article-preview-title").waitFor();
    await retryPage.close();
    for (const fail of [false, true]) {
      const direct = await context.newPage();
      state.articleFeedGate = blockedFeed(fail);
      try {
        await checkArticleFirst(
          direct,
          page.url().split("#")[0] + "#/articles/direct-article",
          state.articleFeedGate,
        );
        if (fail) assert.ok(direct.url().endsWith("#/articles/direct-article"));
      } finally {
        state.articleFeedGate.release();
        state.articleFeedGate = undefined;
        await direct.close();
      }
    }
    await page.bringToFront();
    await page.goto(page.url().split("#")[0] + "#/latest");
    await page.locator(".article-card").first().waitFor();
    assert.equal(
      await page.locator("#main").evaluate((element) => {
        return Math.round(element.getBoundingClientRect().right) === window.innerWidth;
      }),
      true,
    );

    const sidebarToggle = page.getByRole("button", { name: "Expand sidebar", exact: true });
    await sidebarToggle.click();
    assert.equal(
      await page.getByRole("button", { name: "Collapse sidebar", exact: true }).count(),
      1,
    );
    assert.equal(
      await page.evaluate(() => document.documentElement.dataset.sidebarState),
      "expanded",
    );
    assert.equal(
      await page.locator("#main").evaluate((element) => {
        return Math.round(element.getBoundingClientRect().right) === window.innerWidth;
      }),
      true,
    );
    await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();

    assert.deepEqual(await page.locator(".feed-toolbar a").allTextContents(), [
      "All",
      "Articles",
      "News",
      "Tutorials",
      "Releases",
      "Comparisons",
      "Opinions",
    ]);
    assert.equal(await page.locator(".sidebar .nav-item").count(), 7);
    await checkSidebarGitHub(page, path.resolve(extension, `../${browser}-sidebar-github.png`));
    await checkMcp(page, path.resolve(extension, `../${browser}-mcp`));
    const whatsNew = page
      .locator(".sidebar")
      .getByRole("link", { name: "What’s new (opens in a new tab)", exact: true });
    assert.equal(await whatsNew.getAttribute("href"), "https://changelog.devfeed.tech/");
    assert.equal(await whatsNew.getAttribute("target"), "_blank");
    assert.equal(
      await page.getByRole("link", { name: "Sign in", exact: true }).getAttribute("href"),
      "https://devfeed.tech/login?return_to=%2Fextension%2Flogin-complete",
    );
    assert.equal(
      await page.getByRole("link", { name: "Sign in", exact: true }).getAttribute("target"),
      "_blank",
    );
    assert.equal(await page.locator(".article-card .card-image img").count(), 24);
    assert.equal(await page.locator(".article-card .article-share-trigger").count(), 0);
    assert.equal(await page.locator(".discovery-strip").count(), 0);
    assert.equal(
      await page
        .locator(".article-card")
        .first()
        .evaluate((card) => {
          const date = card.querySelector(".card-date");
          const actions = card.querySelector(".article-quick-actions");
          if (!date || !actions) return false;
          const dateBox = date.getBoundingClientRect();
          const actionsBox = actions.getBoundingClientRect();
          const dateCenter = dateBox.top + dateBox.height / 2;
          const actionsCenter = actionsBox.top + actionsBox.height / 2;
          return Math.abs(dateCenter - actionsCenter) < 1 && actionsBox.left > dateBox.right;
        }),
      true,
    );
    assert.equal(
      await page
        .locator(".article-card")
        .first()
        .evaluate((card) => {
          const read = card.querySelector(".article-read-link");
          const bookmark = card.querySelector(".article-bookmark");
          return !!read && !!bookmark && read.textContent.trim() === "Read";
        }),
      true,
    );
    assert.equal(
      await page
        .locator(".article-card")
        .first()
        .evaluate((card) => {
          const actions = card.querySelector(".article-quick-actions");
          const copy = card.querySelector(".card-copy");
          const lastAction = actions?.lastElementChild;
          const contentRight = copy
            ? copy.getBoundingClientRect().right -
              Number.parseFloat(getComputedStyle(copy).paddingRight)
            : 0;
          return (
            !!copy &&
            !!lastAction &&
            Math.abs(lastAction.getBoundingClientRect().right - contentRight) < 1
          );
        }),
      true,
    );
    assert.equal(
      await page.getByRole("searchbox").getAttribute("placeholder"),
      "Search articles, topics, sources, tags",
    );
    await page.screenshot({
      animations: "disabled",
      path: path.join(extension, "../reader-desktop.png"),
    });

    await page
      .locator(".sidebar")
      .getByRole("link", { name: "Explore topics", exact: true })
      .click();
    await page.locator(".topic-card").first().waitFor();
    await page.locator(".topic-card-link").first().click();
    await page.getByRole("heading", { name: "JavaScript", exact: true }).waitFor();
    assert.ok(page.url().endsWith("#/topics/javascript"));
    const follow = page
      .getByRole("region", { name: "Feed controls" })
      .getByRole("link", { name: "Follow", exact: true });
    assert.equal(
      await follow.getAttribute("href"),
      "https://devfeed.tech/login?return_to=%2Fextension%2Flogin-complete",
    );
    assert.equal(await follow.getAttribute("target"), "_blank");
    await checkGuestTopicSignIn(page, page.url().split("#")[0] + "#/topics/javascript", true);
    await page.locator(".article-card").first().waitFor();
    assert.ok(
      requests.some(
        (url) => url.pathname === "/api/v1/feed" && url.searchParams.get("topic") === "javascript",
      ),
    );
    await checkPreviewBackground(page);
    await page.locator(".sidebar").getByRole("link", { name: "Sources", exact: true }).click();
    await page.locator(".source-card-link").first().click();
    await page.getByRole("heading", { name: "Publisher", exact: true }).waitFor();
    assert.ok(page.url().endsWith("#/sources/publisher"));
    await page.locator(".article-card").first().waitFor();
    assert.ok(
      requests.some(
        (url) => url.pathname === "/api/v1/feed" && url.searchParams.get("source_id") === "source",
      ),
    );
    await checkPreviewBackground(page);
    await page.locator(".feed-toolbar").getByRole("link", { name: "News", exact: true }).click();
    await page.locator(".card-open-link").first().waitFor();
    await checkPreviewBackground(page);
    await page.locator(".sidebar").getByRole("link", { name: "Latest feed", exact: true }).click();
    await page.locator(".card-open-link").first().waitFor();

    await checkPreviewBackground(page);

    // The production article-detail route may not be deployed yet. Previews
    // must use public feed data and survive focus changes and full reloads.
    await page.locator(".card-open-link").first().click();
    await page.locator("#article-preview-title").waitFor();
    assert.ok(page.url().includes("#/articles/"));
    await page.getByText("Complete topic description for the article preview.").waitFor();
    await page.getByRole("dialog", { name: "Article preview" }).evaluate((node) => {
      node.dataset.retained = "yes";
    });
    await page.locator(".preview-scroll").evaluate((node) => {
      node.scrollTop = 90;
    });
    const scrollTop = await page.locator(".preview-scroll").evaluate((node) => node.scrollTop);
    const unrelated = await context.newPage();
    await unrelated.goto("about:blank");
    await page.bringToFront();
    // Exercise refresh deterministically even when the headless window manager
    // does not synthesize an OS focus event.
    const rechecked = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/v1/user/auth/me",
    );
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await rechecked;
    assert.equal(
      await page.getByRole("dialog", { name: "Article preview" }).getAttribute("data-retained"),
      "yes",
    );
    assert.equal(
      await page.locator(".preview-scroll").evaluate((node) => node.scrollTop),
      scrollTop,
    );
    await unrelated.close();
    await page.reload();
    await page.locator("#article-preview-title").waitFor();
    await page.getByText("Complete topic description for the article preview.").waitFor();
    assert.equal(await page.getByText("Couldn’t load the article", { exact: true }).count(), 0);
    await page.getByRole("button", { name: "Next article", exact: true }).click();
    await page.waitForURL(/reader-parity-1$/);
    await page.locator("#article-preview-title").waitFor();
    await page.locator(".preview-footer .article-share-trigger").click();
    const reddit = page.getByRole("link", { name: "Share on Reddit (opens in a new tab)" });
    assert.equal(
      new URL(await reddit.getAttribute("href")).searchParams.get("url"),
      "https://devfeed.tech/articles/reader-parity-1",
    );
    await page.keyboard.press("Escape");
    await page.screenshot({
      animations: "disabled",
      path: path.join(extension, "../reader-preview.png"),
    });
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.getByRole("dialog", { name: "Article preview" }).waitFor({ state: "detached" });

    await page.locator(".theme-toggle").click(); // system -> light
    await page.locator(".theme-toggle").click(); // light -> dark
    await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
    const second = await context.newPage();
    await second.goto(newTab);
    await second.waitForFunction(() => document.documentElement.classList.contains("dark"));
    await second.locator(".article-card").first().waitFor();
    // Finish the fixture's intercepted image loads before destroying its document.
    // Otherwise lazy image delivery can race the tab's teardown in Chromium.
    await second.locator("img").evaluateAll(async (images) => {
      for (const image of images) image.loading = "eager";
      await Promise.all(images.map((image) => image.decode()));
    });
    await second.close();
    await page.bringToFront();

    assert.equal(await page.getByRole("combobox", { name: "Language" }).count(), 0);
    await page.getByRole("combobox", { name: "Sort by" }).click();
    await page.getByRole("option", { name: "Most liked" }).click();
    await page.waitForURL(/sort=most_liked/);
    await page.locator(".article-card").first().waitFor();
    assert.ok(
      requests.some(
        (url) => url.pathname === "/api/v1/feed" && url.searchParams.get("sort") === "most_liked",
      ),
    );

    assert.equal(await page.getByRole("link", { name: "More articles", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "More articles", exact: true }).count(), 0);
    await page.locator(".pagination").scrollIntoViewIfNeeded();
    const retry = page.locator(".pagination").getByText("Try again", { exact: true });
    const nextPageArticle = page.getByRole("heading", { name: "Next page article" });
    await retry.or(nextPageArticle).waitFor();
    if (await retry.isVisible()) await retry.click();
    await nextPageArticle.waitFor();
    assert.equal(await page.locator(".article-card").count(), 25);
    assert.equal(await page.locator(".article-grid").count(), 1);
    assert.ok(requests.some((url) => url.searchParams.get("cursor") === "next+page"));

    await page.getByRole("searchbox").fill("python");
    await page.getByRole("heading", { name: "Search result" }).waitFor();
    assert.match(page.url(), /#\/search\?q=python/);
    await page.getByRole("searchbox").fill("python async");
    await page.waitForURL(/q=python\+async/);
    assert.equal(await page.getByRole("searchbox").inputValue(), "python async");
    await page.getByRole("heading", { name: "Search result" }).waitFor();
    const searchUrl = page.url();
    await page.locator(".skip-link").focus();
    await page.keyboard.press("Enter");
    assert.equal(page.url(), searchUrl, "skipping content preserves the search route");
    assert.equal(await page.evaluate(() => document.activeElement?.id), "main");
    assert.equal(await page.getByRole("heading", { name: "Search result" }).count(), 1);

    await checkSearchInfiniteScroll(page, page.url().split("#")[0] + "#/search?q=infinite-scroll");
    await checkSearchFilters(page, page.url().split("#")[0] + "#/search?q=microservice");
    await checkSearchSecurity(
      page,
      page.url().split("#")[0] + `#/search?${new URLSearchParams({ q: searchSecurityQuery })}`,
    );
    await checkPreviewBackground(page, undefined, true);

    await page.goto(newTab);
    await page.locator(".article-card").first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await checkPreviewBackground(page);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    assert.equal(await page.locator(".mobile-nav").isVisible(), true);
    assert.equal(await page.getByRole("link", { name: "Read later", exact: true }).count(), 0);
    assert.equal(await page.locator(".mobile-nav").getByRole("link", { name: "Legal" }).count(), 0);
    await page.screenshot({
      animations: "disabled",
      path: path.join(extension, "../reader-mobile.png"),
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    state.engagementPagination = true;
    await checkEngagementPagination(page, newTab);
    state.engagementPagination = false;
  },
);

test("route styles are present in built new tabs", { timeout: 180000 }, async (t) => {
  const { page, pathFor } = await readerFixture(t);
  await checkRouteStyles(page, pathFor, true);
});
test(
  "reader DOM stays bounded per card while retaining long catalogs in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, domCatalog } = await readerFixture(t);
    await checkReaderDom(
      page,
      pathFor,
      domCatalog,
      path.resolve(import.meta.dirname, `../../../reports/reader-dom/${browser}`),
    );
  },
);

test(
  "guest document displays its shell before fresh results in built new tabs",
  { timeout: 180000 },
  async (t) => {
    const { page, pathFor, state } = await readerFixture(t);
    await checkGuestDocument(
      page,
      pathFor,
      (gate) => {
        state.articleFeedGate = gate;
      },
      path.resolve(import.meta.dirname, `../../../reports/document-latency/${browser}`),
    );
  },
);

test("tag directory and detail pages work in built new tabs", { timeout: 180000 }, async (t) => {
  const { page, pathFor } = await readerFixture(t);
  await checkTags(page, pathFor, `/tmp/devfeed-tags/${browser}`);
});
