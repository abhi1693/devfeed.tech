import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { checkPreviewBackground } from "./preview-background.mjs";

const formats = [
  ["articles", "article", "Articles"],
  ["news", "news", "News"],
  ["tutorials", "tutorial", "Tutorials"],
  ["releases", "release", "Releases"],
  ["comparisons", "comparison", "Comparisons"],
  ["opinions", "opinion", "Opinions"],
];
const apiPath = (url) => url.pathname.replace(/^\/api(?=\/v1\/)/, "");
const feedRequest = (url) => apiPath(url) === "/v1/feed";
const backgroundRequest = (url) =>
  ["/v1/feed", "/v1/feed/options", "/v1/user/feed"].includes(apiPath(url));

/** Deterministic data and server-side request evidence for all three reader bundles. */
export function readerRoutesFixture(article) {
  const topic = {
    ...article.topics[0],
    id: "77777777-7777-4777-8777-777777777777",
    slug: "javascript",
    name: "JavaScript",
    kind: "language",
    description: "JavaScript route fixture",
    ai_description: null,
    logo_url: null,
    logo_variants: [],
  };
  const source = {
    ...article.sources[0],
    id: "99999999-9999-4999-8999-999999999999",
    slug: "publisher",
    name: "Publisher",
    logo_url: null,
    logo_variants: [],
  };
  const articles = formats.map(([, content_type], index) => ({
    ...article,
    id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
    slug: `route-fixture-${content_type}`,
    title: `Route fixture: ${content_type}`,
    content_type,
    topics: [topic],
    sources: [source],
  }));
  const operations = new Map([
    [
      "/v1/feed",
      (url) => ({
        items: articles.filter(
          (item) =>
            !url.searchParams.has("content_type") ||
            item.content_type === url.searchParams.get("content_type"),
        ),
        next_cursor: null,
      }),
    ],
    [
      "/v1/feed/options",
      () => ({
        sources: [source],
        content_types: formats.map(([, type]) => type),
        languages: ["en"],
      }),
    ],
    ["/v1/topics/javascript", () => topic],
    ["/v1/sources/publisher", () => source],
    ["/v1/topics/missing-topic", () => ({ detail: "Not found" })],
    ["/v1/sources/missing-source", () => ({ detail: "Not found" })],
    ...articles.map((item) => [
      `/v1/articles/${item.slug}`,
      (url) => (url.pathname.startsWith("/api/") ? { article: item, topic } : item),
    ]),
  ]);
  return {
    active: false,
    topic,
    source,
    articles,
    requests: [],
    record(url) {
      if (
        this.active &&
        (backgroundRequest(url) ||
          apiPath(url).startsWith("/v1/topics") ||
          apiPath(url).startsWith("/v1/sources"))
      )
        this.requests.push(url);
    },
    handles(url) {
      return this.active && operations.has(apiPath(url));
    },
    respond(url) {
      const path = apiPath(url);
      return {
        status: ["/v1/topics/missing-topic", "/v1/sources/missing-source"].includes(path)
          ? 404
          : 200,
        json: operations.get(path)(url),
      };
    },
  };
}

/** Invalid paths and valid content filters behave identically in web, Chrome, and Edge. */
export async function checkReaderRoutes(page, destination, fixture, output, extension = false) {
  await mkdir(output, { recursive: true });
  const originalViewport = page.viewportSize();
  const missing = page.getByRole("heading", { name: "Page not found", exact: true });
  fixture.active = true;
  const currentRoute = () => {
    const url = new URL(page.url());
    return extension ? new URL(url.hash.slice(1) || "/", "https://devfeed.tech") : url;
  };
  const checkFeed = async (type, scope) => {
    const expected = fixture.articles
      .filter((item) => !type || item.content_type === type)
      .map((item) => item.title);
    await page.waitForFunction(
      (titles) =>
        JSON.stringify(
          [...document.querySelectorAll(".article-card h2")].map((heading) =>
            heading.textContent.trim(),
          ),
        ) === JSON.stringify(titles),
      expected,
    );
    assert.deepEqual(await page.locator(".article-card h2").allTextContents(), expected);
    const label = formats.find(([, value]) => value === type)?.[2] ?? "All";
    assert.equal(await page.locator('.feed-toolbar a[aria-current="page"]').textContent(), label);
    const requests = fixture.requests.filter(feedRequest);
    assert.ok(
      requests.some(
        (url) =>
          (url.searchParams.get("content_type") ?? "") === (type ?? "") &&
          (url.searchParams.get("topic") ?? "") ===
            (scope === "topics" ? fixture.topic.slug : "") &&
          (url.searchParams.get("source_id") ?? "") ===
            (scope === "sources" ? fixture.source.id : ""),
      ),
      `${scope ?? "latest"} ${type ?? "All"} preserves the API content and catalog scope`,
    );
    assert.equal(await missing.count(), 0);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
  };
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const path of [
      "/topics/javascript/unknown-format",
      "/sources/publisher/unknown-format",
      "/not-a-reader-page",
      "/settings/unknown-section",
      "/topics/javascript/tutorials/extra",
      "/sources/publisher/news/extra",
      "/topics/javascript/tutorial",
      "/topics/c%2B%2B/news",
      "/topics/c%23/news",
      "/topics/bad%20slug/news",
      "/sources/publisher/news%2Fextra",
      "/topics/javascript/news%5Cextra",
    ]) {
      const before = fixture.requests.length;
      const response = await page.goto(destination(path));
      await missing.waitFor();
      // Allow client effects to settle so the absence of a fallback request is observed.
      await page.waitForTimeout(100);
      assert.equal(
        await page.locator(".article-card").count(),
        0,
        `${path} does not render a feed`,
      );
      assert.deepEqual(
        fixture.requests.slice(before).map((url) => url.pathname + url.search),
        [],
        `${path} makes no feed, options, or catalog item requests`,
      );
      if (!extension) {
        assert.ok([200, 404].includes(response.status()), "Next may stream the not-found response");
        await page
          .locator('meta[name="robots"][content*="noindex"]')
          .waitFor({ state: "attached" });
      }
    }
    if (extension) {
      for (const hash of ["http://[", "//outside.test/latest"]) {
        const before = fixture.requests.length;
        await page.goto(destination(hash));
        await missing.waitFor();
        await page.waitForTimeout(100);
        assert.equal(await page.locator(".article-card").count(), 0);
        assert.deepEqual(
          fixture.requests.slice(before),
          [],
          `The malformed or nonlocal hash ${hash} makes no reader requests`,
        );
      }
    }
    for (const [path, detail] of [
      ["/topics/missing-topic", "/v1/topics/missing-topic"],
      ["/sources/missing-source", "/v1/sources/missing-source"],
    ]) {
      const before = fixture.requests.length;
      await page.goto(destination(path));
      await missing.waitFor();
      assert.equal(await page.locator(".article-card").count(), 0);
      const observed = fixture.requests.slice(before);
      assert.ok(
        observed.some((url) => apiPath(url) === detail),
        `${path} resolves the catalog item`,
      );
      assert.ok(
        observed.every((url) => apiPath(url) === detail),
        `${path} rejects missing catalog items before requesting feed data`,
      );
    }
    await page.screenshot({ path: `${output}/missing-desktop.png`, animations: "disabled" });
    const recover = page.getByRole("link", { name: "Discover something else", exact: true });
    assert.equal(await recover.getAttribute("href"), extension ? "#/" : "/");
    await recover.click();
    await checkFeed(undefined);
    assert.equal(currentRoute().pathname, "/", "Recovery opens the reader home");
    if (extension) assert.match(page.url(), /^chrome-extension:\/\/[^/]+\/.*#\/$/);
    assert.equal(await page.evaluate(() => window.scrollY), 0);

    for (const [suffix, type] of formats) {
      await page.goto(destination(`/${suffix}`));
      await checkFeed(type);
    }
    for (const scope of ["topics", "sources"]) {
      const slug = scope === "topics" ? fixture.topic.slug : fixture.source.slug;
      await page.goto(destination(`/${scope}/${slug}`));
      await checkFeed(undefined, scope);
      for (const [suffix, type] of formats) {
        await page.goto(destination(`/${scope}/${slug}/${suffix}`));
        await checkFeed(type, scope);
        assert.equal(currentRoute().pathname, `/${scope}/${slug}/${suffix}`);
      }
      await page.locator(".feed-toolbar").getByRole("link", { name: "All", exact: true }).click();
      await checkFeed(undefined, scope);
      assert.equal(currentRoute().pathname, `/${scope}/${slug}`, "All retains the catalog scope");
    }
    await checkPreviewBackground(page, `${output}/valid-preview.png`);

    if (extension) {
      await page.goto(destination("/topics/javascript/unknown-format"));
      await missing.waitFor();
      const before = fixture.requests.length;
      await page.evaluate((slug) => {
        history.pushState(
          { readerBackground: "/topics/javascript/unknown-format" },
          "",
          `#/articles/${slug}`,
        );
        window.dispatchEvent(new Event("devfeed:extension-route"));
      }, fixture.articles[0].slug);
      await page.locator("#article-preview-title").waitFor();
      assert.equal(await page.locator(".standalone-state h1").textContent(), "Page not found");
      assert.equal(await page.locator(".article-card").count(), 0);
      assert.deepEqual(
        fixture.requests.slice(before).filter(backgroundRequest),
        [],
        "An invalid saved preview background never becomes an unfiltered feed",
      );
      await page.screenshot({
        path: `${output}/invalid-preview-background.png`,
        animations: "disabled",
      });
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await missing.waitFor();
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(destination("/sources/publisher/unknown-format"));
    await missing.waitFor();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page.screenshot({ path: `${output}/missing-mobile.png`, animations: "disabled" });
    await page.getByRole("link", { name: "Discover something else", exact: true }).click();
    await checkFeed(undefined);
    console.log(
      "Shared reader route validation, scoped content filters, and missing-page recovery passed.",
    );
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    console.error("Reader route failure", page.url(), await page.locator("body").innerText());
    throw error;
  } finally {
    fixture.active = false;
    await page.setViewportSize(originalViewport);
  }
}
