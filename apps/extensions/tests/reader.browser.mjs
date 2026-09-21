import { checkPreviewBackground } from "../../../scripts/testing/preview-background.mjs";
import {
  searchFixture,
  checkSearchFilters,
  checkSearchInfiniteScroll,
} from "../../../scripts/testing/search-filters.mjs";
import {
  withManagedImage,
  mockManagedImages,
  checkManagedImages,
} from "../../../scripts/testing/managed-images.mjs";
import { signInResponse, checkGuestTopicSignIn } from "../../../scripts/testing/sign-in.mjs";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";

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

// Run against a real unpacked extension; browser requests are deterministic and
// never depend on the production feed or mutate visitor/account data.
test(
  "shared reader works inside new tabs at desktop and mobile sizes",
  { timeout: 60000 },
  async () => {
    const profile = await mkdtemp(path.join(tmpdir(), "devfeed-reader-test-"));
    const context = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        ? undefined
        : browser === "edge"
          ? "msedge"
          : "chromium",
      headless: true,
      viewport: { width: 1440, height: 1000 },
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    });
    await mockManagedImages(context);
    const errors = [];
    const requests = [];
    const analytics = [];
    const feedItems = Array.from({ length: 24 }, (_, index) => ({
      ...article,
      id: `${article.id}-${index}`,
      slug: `${article.slug}-${index}`,
    }));
    let failNextPage = true;
    let failArticle = true;
    await context.route("https://identity.example/authorize?**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<p>Sign-in provider</p>" }),
    );
    await context.route("https://devfeed.tech/api/**", async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      if (["/api/v1/feed", "/api/v1/topics", "/api/v1/sources"].includes(url.pathname))
        assert.equal(route.request().headers()["cache-control"], "max-age=600");
      if (url.pathname === "/api/v1/articles/retry-article") {
        return failArticle
          ? route.fulfill({ status: 503, json: {} })
          : route.fulfill({
              json: { article: { ...article, slug: "retry-article" }, topic: null },
            });
      }
      if (url.pathname === "/api/v1/extension/analytics") {
        if (route.request().method() === "POST") {
          analytics.push(route.request().postDataJSON());
          return route.fulfill({ status: 204 });
        }
        return route.fulfill({ json: { enabled: true } });
      }
      if (url.pathname === "/api/v1/user/auth/login")
        return route.fulfill(await signInResponse(url.pathname.slice(4) + url.search));
      let json;
      if (url.pathname === "/api/v1/feed") {
        if (
          url.searchParams.has("cursor") &&
          url.searchParams.get("sort") === "most_liked" &&
          failNextPage
        ) {
          failNextPage = false;
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
      } else if (url.pathname === "/api/v1/articles/direct-article") {
        json = { article: { ...feedItems[0], slug: "direct-article" }, topic: null };
      } else if (url.pathname === "/api/v1/user/auth/me") {
        json = null;
      } else if (url.pathname === "/api/v1/feed/options") {
        json = {
          sources: article.sources,
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
        ["microservice", "infinite-scroll"].includes(url.searchParams.get("q"))
      ) {
        if (url.searchParams.has("sort")) await new Promise((resolve) => setTimeout(resolve, 800));
        json = searchFixture(url.searchParams.get("q"), url.searchParams.get("sort"));
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
      return route.fulfill({ json });
    });
    try {
      const page = await context.newPage();
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error" && /Content Security Policy|Refused to/.test(message.text()))
          errors.push(message.text());
      });
      await page.goto(newTab);
      await page.locator(".article-card").first().waitFor();
      await checkManagedImages(page);
      assert.ok(page.url().startsWith("chrome-extension://"));
      await page.waitForURL(/#\/latest$/);
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
      await tagged.goto(page.url().split("#")[0] + `#/?${campaign}&unrelated=discard`);
      await tagged.waitForURL(new RegExp(`#\\/latest\\?${campaign}$`));
      await tagged.locator(".article-card").first().waitFor();
      await tagged.close();
      const retryPage = await context.newPage();
      await retryPage.goto(page.url().split("#")[0] + "#/articles/retry-article");
      await retryPage.getByRole("heading", { name: "Couldn’t load the article" }).waitFor();
      failArticle = false;
      await retryPage.getByRole("button", { name: "Try again", exact: true }).click();
      await retryPage.locator("#article-preview-title").waitFor();
      await retryPage.close();
      const direct = await context.newPage();
      await direct.goto(page.url().split("#")[0] + "#/articles/direct-article");
      await direct.locator("#article-preview-title").waitFor();
      assert.ok(direct.url().endsWith("#/articles/direct-article"));
      await direct.close();
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
      assert.equal(await page.locator(".sidebar .nav-item").count(), 4);
      const whatsNew = page
        .locator(".sidebar")
        .getByRole("link", { name: "What’s new (opens in a new tab)", exact: true });
      assert.equal(await whatsNew.getAttribute("href"), "https://changelog.devfeed.tech/");
      assert.equal(await whatsNew.getAttribute("target"), "_blank");
      assert.equal(
        await page.getByRole("link", { name: "Sign in", exact: true }).getAttribute("href"),
        "https://devfeed.tech/api/v1/user/auth/login?return_to=%2Fextension%2Flogin-complete",
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
        "https://devfeed.tech/api/v1/user/auth/login?return_to=%2Fextension%2Flogin-complete",
      );
      assert.equal(await follow.getAttribute("target"), "_blank");
      await checkGuestTopicSignIn(page, page.url().split("#")[0] + "#/topics/javascript", true);
      await page.locator(".article-card").first().waitFor();
      assert.ok(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/feed" && url.searchParams.get("topic") === "javascript",
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
          (url) =>
            url.pathname === "/api/v1/feed" && url.searchParams.get("source_id") === "source",
        ),
      );
      await checkPreviewBackground(page);
      await page.locator(".feed-toolbar").getByRole("link", { name: "News", exact: true }).click();
      await page.locator(".card-open-link").first().waitFor();
      await checkPreviewBackground(page);
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Latest feed", exact: true })
        .click();
      await page.locator(".card-open-link").first().waitFor();

      await checkPreviewBackground(page);

      // The production article-detail route may not be deployed yet. Previews
      // must use public feed data and survive focus changes and full reloads.
      await page.locator(".card-open-link").first().click();
      await page.locator("#article-preview-title").waitFor();
      assert.ok(page.url().includes("#/articles/"));
      await page.getByText("Complete topic description for the article preview.").waitFor();
      await page.locator("dialog").evaluate((node) => {
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
      assert.equal(await page.locator("dialog").getAttribute("data-retained"), "yes");
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
      await page.locator("dialog").waitFor({ state: "detached" });

      await page.locator(".theme-toggle").click(); // system -> light
      await page.locator(".theme-toggle").click(); // light -> dark
      await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
      const second = await context.newPage();
      await second.goto(newTab);
      await second.waitForFunction(() => document.documentElement.classList.contains("dark"));
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
      assert.equal(
        await page.getByRole("button", { name: "More articles", exact: true }).count(),
        0,
      );
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

      await checkSearchInfiniteScroll(
        page,
        page.url().split("#")[0] + "#/search?q=infinite-scroll",
      );
      await checkSearchFilters(page, page.url().split("#")[0] + "#/search?q=microservice");
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
      assert.equal(
        await page.locator(".mobile-nav").getByRole("link", { name: "Legal" }).count(),
        0,
      );
      await page.screenshot({
        animations: "disabled",
        path: path.join(extension, "../reader-mobile.png"),
      });
      assert.deepEqual(errors, []);
    } finally {
      await context.close();
      await rm(profile, { recursive: true, force: true });
    }
  },
);
