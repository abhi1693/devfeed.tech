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
    const errors = [];
    const requests = [];
    const feedItems = Array.from({ length: 24 }, (_, index) => ({
      ...article,
      id: `${article.id}-${index}`,
      slug: `${article.slug}-${index}`,
    }));
    let failNextPage = true;
    await context.route("https://devfeed.tech/api/**", async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      let json;
      if (url.pathname === "/api/v1/feed") {
        if (url.searchParams.has("cursor") && failNextPage) {
          failNextPage = false;
          return route.fulfill({ status: 503, json: {} });
        }
        json = url.searchParams.has("cursor")
          ? {
              items: [{ ...article, id: "next", slug: "next", title: "Next page article" }],
              next_cursor: null,
            }
          : { items: feedItems, next_cursor: "next+page" };
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
      } else if (url.pathname === "/api/v1/sources") {
        json = { items: article.sources, next_cursor: null };
      } else if (url.pathname === "/api/v1/user/engagement") {
        json = feedItems.map((item) => ({ article_id: item.id, likes: 2, opens: 5, liked: false }));
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
      assert.ok(page.url().startsWith("chrome-extension://"));
      await page.waitForURL(/#\/latest$/);
      assert.equal(
        await page.getByRole("dialog", { name: "A fresh feed in every new tab." }).count(),
        0,
      );
      assert.equal(await page.getByRole("link", { name: "Read later", exact: true }).count(), 0);
      const direct = await context.newPage();
      await direct.goto(page.url().split("#")[0] + "#/articles/direct-article");
      await direct.locator("#article-preview-title").waitFor();
      assert.ok(direct.url().endsWith("#/articles/direct-article"));
      await direct.close();
      await page.bringToFront();
      await page.goto(page.url().split("#")[0] + "#/latest");
      await page.locator(".article-card").first().waitFor();

      assert.deepEqual(await page.locator(".feed-toolbar a").allTextContents(), [
        "All",
        "Articles",
        "News",
        "Tutorials",
        "Releases",
        "Comparisons",
        "Opinions",
      ]);
      assert.equal(await page.locator(".sidebar .nav-item").count(), 3);
      assert.equal(
        await page.getByRole("link", { name: "Sign in", exact: true }).getAttribute("href"),
        "https://devfeed.tech/api/v1/user/auth/login",
      );
      assert.equal(
        await page.getByRole("link", { name: "Sign in", exact: true }).getAttribute("target"),
        "_blank",
      );
      assert.equal(await page.locator(".article-card .image-placeholder svg").count(), 24);
      assert.equal(await page.locator(".article-card .article-share-trigger").count(), 24);
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
      await page.locator(".topic-card").first().click();
      await page.getByRole("heading", { name: "JavaScript", exact: true }).waitFor();
      assert.ok(page.url().endsWith("#/topics/javascript"));
      await page.locator(".article-card").first().waitFor();
      assert.ok(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/feed" && url.searchParams.get("topic") === "javascript",
        ),
      );
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
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Latest feed", exact: true })
        .click();
      await page.locator(".card-open-link").first().waitFor();

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

      await page.locator(".article-share-trigger").first().click();
      const reddit = page.getByRole("link", { name: "Share on Reddit (opens in a new tab)" });
      assert.equal(
        new URL(await reddit.getAttribute("href")).searchParams.get("url"),
        "https://devfeed.tech/articles/reader-parity-0",
      );
      await page.keyboard.press("Escape");

      await page.locator(".filter-menu summary").click();
      await page.getByRole("combobox", { name: "Language" }).click();
      await page.getByRole("option", { name: "French" }).click();
      await page.getByRole("button", { name: "Apply filters" }).click();
      await page.waitForURL(/language=fr/);
      await page.locator(".article-card").first().waitFor();
      assert.ok(
        requests.some(
          (url) => url.pathname === "/api/v1/feed" && url.searchParams.get("language") === "fr",
        ),
      );

      await page.locator(".pagination").scrollIntoViewIfNeeded();
      await page.getByRole("link", { name: "Try again", exact: true }).waitFor();
      await page.getByRole("link", { name: "Try again", exact: true }).click();
      await page.getByRole("heading", { name: "Next page article" }).waitFor();
      assert.equal(await page.locator(".article-card").count(), 25);
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

      await page.goto(newTab);
      await page.locator(".article-card").first().waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      assert.equal(await page.locator(".mobile-nav").isVisible(), true);
      assert.equal(await page.getByRole("link", { name: "Read later", exact: true }).count(), 0);
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
