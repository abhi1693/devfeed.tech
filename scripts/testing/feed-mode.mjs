import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { checkPreviewBackground } from "./preview-background.mjs";
import { readerRoutesFixture } from "./reader-routes.mjs";

const apiPath = (url) => url.pathname.replace(/^\/api(?=\/v1\/)/, "");
const placementPaths = ["/v1/partner-placements/query", "/v1/user/partner-placements/query"];
const feedPaths = ["/v1/feed", "/v1/user/feed"];

/** Observe transport evidence while exercising the same real reader UI in all runtimes. */
export function feedModeFixture(article) {
  const catalog = readerRoutesFixture(article);
  const articles = catalog.articles.flatMap((item, index) =>
    Array.from({ length: 4 }, (_, copy) => ({
      ...item,
      id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index * 4 + copy).padStart(12, "0")}`,
      slug: `${item.slug}-${copy}`,
      title: `${item.title} ${copy + 1}`,
    })),
  );
  const feed = (url) => ({
    items: articles.filter(
      (item) =>
        !url.searchParams.has("content_type") ||
        item.content_type === url.searchParams.get("content_type"),
    ),
    next_cursor: null,
  });
  const operations = new Map([
    ["/v1/feed", feed],
    [`/v1/sources/${catalog.source.id}`, () => catalog.source],
    ["/v1/tags/typescript", () => ({ ...catalog.topic, slug: "typescript", name: "TypeScript" })],
    [
      "/v1/user/auth/me",
      () => ({
        user_id: "feed-mode-reader",
        name: "Feed mode reader",
        email: "reader@example.test",
        csrf_token: "test",
        expires_at: Date.now() / 1000 + 3600,
      }),
    ],
    [
      "/v1/user/settings/profile",
      () => ({
        display_name: "Feed mode reader",
        username: "feed-reader",
        avatar_url: null,
        stack: [],
      }),
    ],
    [
      "/v1/user/settings/feed",
      () => ({
        view: "cards",
        content_types: ["article", "news", "tutorial", "release", "comparison", "opinion"],
        languages: ["en"],
      }),
    ],
    ["/v1/user/settings/appearance", () => ({ theme: "light" })],
    ["/v1/user/preferences", () => ({ topic_ids: [catalog.topic.id] })],
    ["/v1/user/preferences/sources", () => ({ source_ids: [] })],
    ["/v1/user/notifications/config", () => ({ enabled: false })],
    [
      "/v1/user/engagement",
      () =>
        articles.map((item) => ({
          article_id: item.id,
          likes: 0,
          opens: 0,
          liked: false,
          bookmarked: false,
        })),
    ],
    [
      "/v1/user/feed",
      (url) => ({
        ...feed(url),
        status: "ready",
        generation: "feed-mode",
        has_interests: true,
        reasons: {},
        feed_kind: "personalized",
      }),
    ],
    [
      "/v1/search",
      (url) => ({
        query: url.searchParams.get("q"),
        sections: {
          articles: {
            items: articles.map((item) => ({
              id: item.id,
              title: item.title,
              description: item.summary,
              href: `/articles/${item.slug}`,
              image_url: null,
              label: item.content_type,
              published_at: item.published_at,
            })),
            next_cursor: null,
          },
        },
      }),
    ],
    ...articles.map((item) => [
      `/v1/articles/${item.slug}`,
      (url) => (url.pathname.startsWith("/api/") ? { article: item, topic: catalog.topic } : item),
    ]),
    ...placementPaths.map((path) => [path, () => ({ items: [] })]),
  ]);
  return {
    active: false,
    signedIn: false,
    articles,
    topic: catalog.topic,
    source: catalog.source,
    requests: [],
    queries: [],
    handles(url) {
      catalog.active = this.active;
      return this.active && (operations.has(apiPath(url)) || catalog.handles(url));
    },
    respond(url, payload) {
      const path = apiPath(url);
      if (feedPaths.includes(path)) this.requests.push(url);
      if (placementPaths.includes(path)) this.queries.push({ path, body: payload });
      if (path === "/v1/user/auth/me" && !this.signedIn) return { status: 200, json: null };
      return operations.has(path)
        ? { status: 200, json: operations.get(path)(url) }
        : catalog.respond(url);
    },
  };
}

export async function checkFeedMode(page, destination, fixture, output, extension = false) {
  await mkdir(output, { recursive: true });
  const viewport = page.viewportSize();
  fixture.active = true;
  // Keep unrelated invitation flows out of this route-capability journey.
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  const check = async ({
    path,
    fallback,
    disabled = false,
    surface = "feed",
    type,
    personal = false,
    formats,
  }) => {
    fixture.requests.length = 0;
    fixture.queries.length = 0;
    await page.goto("about:blank");
    await page.goto(destination(path));
    const search = path.startsWith("/search");
    const titles = fixture.articles
      .filter((item) => !type || item.content_type === type)
      .map((item) => item.title);
    const heading = search ? ".search-result-heading" : ".article-card h2";
    await page.waitForFunction(
      ({ selector, titles }) =>
        JSON.stringify(
          [...document.querySelectorAll(selector)].map((node) => node.textContent.trim()),
        ) === JSON.stringify(titles),
      { selector: heading, titles },
    );
    if (!search) {
      assert.equal(
        await page.locator('.feed-toolbar a[aria-current="page"]').first().textContent(),
        type === "news" ? "News" : type === "tutorial" ? "Tutorials" : "All",
        `${path}: effective content filter`,
      );
      const request = fixture.requests.find(
        (url) => apiPath(url) === (personal ? "/v1/user/feed" : "/v1/feed"),
      );
      assert.ok(request, `${path}: ${personal ? "personal" : "public"} feed mode`);
      assert.equal(
        request.searchParams.get("content_type") ?? "",
        type ?? "",
        `${path}: no implicit Articles restriction`,
      );
    }
    if (disabled) {
      await page.waitForTimeout(200);
      assert.deepEqual(
        fixture.queries.filter((query) => query.body.surface !== "header"),
        [],
        `${path}: paginated routes do not request contextual placements`,
      );
    } else {
      await page.waitForFunction(
        () => document.querySelectorAll('.reader-loading-reveal[data-loading="true"]').length === 0,
      );
      for (
        let attempt = 0;
        attempt < 100 && !fixture.queries.some((query) => query.body.surface === surface);
        attempt++
      )
        await page.waitForTimeout(20);
      const query = fixture.queries.find((query) => query.body.surface === surface);
      assert.ok(query, `${path}: ${surface} placement policy requested`);
      assert.equal(
        query.path,
        fixture.signedIn ? "/v1/user/partner-placements/query" : "/v1/partner-placements/query",
        `${path}: session transport`,
      );
      assert.equal(
        Boolean(query.body.allow_sponsored_fallback),
        fallback,
        `${path}: sponsored fallback eligibility`,
      );
      assert.equal(
        query.body.ad_formats.includes("header_sponsorship"),
        false,
        `${path}: contextual inventory excludes Header sponsorship`,
      );
      if (surface === "topic") assert.equal(query.body.topic_id, fixture.topic.id);
      if (formats) {
        assert.deepEqual(
          query.body.ad_formats,
          formats,
          `${path}: placement inventory is explicit`,
        );
        assert.equal(
          fixture.queries.filter((query) => query.body.surface !== "header").length,
          1,
          `${path}: one contextual placement query`,
        );
        if (surface === "search") {
          assert.equal(query.body.article_ids, undefined);
          assert.deepEqual(query.body.search, {
            query: "typescript",
            results: { articles: fixture.articles.map((item) => item.id) },
          });
        } else
          assert.deepEqual(
            query.body.article_ids,
            fixture.articles.slice(0, 24).map((item) => item.id),
          );
      }
    }
    const wide = page.viewportSize().width >= 1280;
    if (wide) {
      for (
        let attempt = 0;
        attempt < 100 && !fixture.queries.some((query) => query.body.surface === "header");
        attempt++
      )
        await page.waitForTimeout(20);
      const headers = fixture.queries.filter((query) => query.body.surface === "header");
      assert.equal(headers.length, 1, `${path}: independent sitewide Header selection`);
      const { exclude_product_ids, ...selection } = headers[0].body;
      assert.deepEqual(selection, { surface: "header", ad_formats: ["header_sponsorship"] });
      assert.ok(Array.isArray(exclude_product_ids));
      assert.equal(
        headers[0].path,
        fixture.signedIn ? "/v1/user/partner-placements/query" : "/v1/partner-placements/query",
        `${path}: Header selection uses the same session boundary`,
      );
    } else {
      assert.equal(
        fixture.queries.filter((query) => query.body.surface === "header").length,
        0,
        `${path}: narrow navigation reserves no Header inventory`,
      );
    }
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
      `${path}: no horizontal overflow`,
    );
  };
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const options of [
      { path: "/", fallback: true },
      { path: "/latest", fallback: true },
      { path: "/latest?sort=newest", fallback: true },
      { path: "/?content_type=invalid", fallback: true },
      { path: "/latest?source_id=invalid", fallback: true },
      { path: "/news", fallback: false, type: "news" },
      { path: "/tutorials", fallback: false, type: "tutorial" },
      { path: "/?q=typescript", fallback: false },
      { path: "/latest?topic=javascript", fallback: false, surface: extension ? "feed" : "topic" },
      { path: "/latest?tag=typescript", fallback: false },
      { path: "/topics/javascript", fallback: false, surface: "topic" },
      { path: "/topics/javascript/news", fallback: false, surface: "topic", type: "news" },
      { path: "/sources/publisher", fallback: false, formats: ["feed", "sponsored_content"] },
      {
        path: `/latest?source_id=${fixture.source.id}`,
        fallback: false,
        formats: ["feed", "sponsored_content"],
      },
      { path: "/latest?cursor=next-page", disabled: true },
      { path: "/search?q=typescript", fallback: false, surface: "search", formats: ["engagement"] },
    ])
      await check(options);
    // A preview must retain the validated mode and its loaded background, including contextual routes.
    for (const options of [
      { path: "/latest", fallback: true },
      { path: "/topics/javascript", fallback: false, surface: "topic" },
    ]) {
      await check(options);
      const requests = fixture.requests.length;
      await checkPreviewBackground(page, `${output}/${options.surface ?? "feed"}-preview.png`);
      assert.equal(
        fixture.requests.length,
        requests,
        `${options.path}: preview retains feed mode without reloading`,
      );
    }
    fixture.signedIn = true;
    if (!extension)
      await page
        .context()
        .addCookies([{ name: "devfeed_user_session", value: "valid", url: destination("/") }]);
    for (const options of [
      { path: "/", fallback: true, personal: true },
      { path: "/?content_type=invalid", fallback: true, personal: true },
      { path: "/?q=typescript", fallback: false, personal: true },
      { path: "/?content_type=news", fallback: false, personal: true, type: "news" },
      {
        path: `/?source_id=${fixture.source.id}`,
        fallback: false,
        personal: true,
        formats: ["feed", "sponsored_content"],
      },
      { path: "/?cursor=next-page", disabled: true, personal: true },
      { path: "/latest", fallback: true },
      { path: "/news", fallback: false, type: "news" },
      { path: "/tutorials", fallback: false, type: "tutorial" },
      { path: "/latest?q=typescript", fallback: false },
      { path: "/latest?tag=typescript", fallback: false },
      { path: "/topics/javascript", fallback: false, surface: "topic" },
      { path: "/topics/javascript/news", fallback: false, surface: "topic", type: "news" },
      { path: "/sources/publisher", fallback: false, formats: ["feed", "sponsored_content"] },
      {
        path: `/latest?source_id=${fixture.source.id}`,
        fallback: false,
        formats: ["feed", "sponsored_content"],
      },
      { path: "/latest?cursor=next-page", disabled: true },
      { path: "/search?q=typescript", fallback: false, surface: "search", formats: ["engagement"] },
    ])
      await check(options);
    await page.setViewportSize({ width: 390, height: 844 });
    await check({ path: "/", fallback: true, personal: true });
    await page.screenshot({
      path: `${output}/personal-mobile.png`,
      fullPage: true,
      animations: "disabled",
    });
    console.log(
      "Shared feed modes, normalized filters, placement capabilities, and preview backgrounds passed.",
    );
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    console.error(
      "Feed mode failure",
      page.url(),
      await page.locator("body").innerText(),
      fixture.requests.map(String),
      fixture.queries,
    );
    throw error;
  } finally {
    fixture.active = false;
    fixture.signedIn = false;
    await page.setViewportSize(viewport);
  }
}
