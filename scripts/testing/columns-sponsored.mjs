import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { chooseInlineArticleView, readerRouteUrl } from "./article-view.mjs";
import { columnsCatalog } from "./columns-workspace.mjs";
import { columnsFixtureArticle, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";

const signingKey = createHmac("sha256", "disposable-columns-placement-browser-key")
  .update("devfeed:partner-delivery:v1")
  .digest();
const productIds = Object.fromEntries(
  ["shared", "source", "topic", "tag"].map((kind, index) => [
    kind,
    `91111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}`,
  ]),
);
const uuid = /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/;

async function waitFor(check, message) {
  const deadline = Date.now() + 12000;
  while (!(await check())) {
    assert.ok(Date.now() < deadline, message);
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
}

function kindFor(query) {
  if (query.surface === "topic") return "topic";
  if (query.article_ids?.some((id) => id.startsWith("bbbbbbbb-0007-"))) return "source";
  if (query.article_ids?.some((id) => id.startsWith("bbbbbbbb-0014-"))) return "tag";
  if (query.article_ids?.some((id) => id.startsWith("bbbbbbbb-0005-"))) return "recommended";
  return "latest";
}

/** Serve eligible products with real signed-receipt shapes and validate public tracking. */
async function installSponsoredProducts(page) {
  const context = page.context();
  const queries = [];
  const events = [];
  const receipts = new Set();
  let sequence = 0;
  const product = (kind) => {
    const index = ["shared", "source", "topic", "tag"].indexOf(kind) + 1;
    const asset = `92222222-2222-4222-8222-${String(index).padStart(12, "0")}`;
    const delivery = `94444444-4444-4444-8444-${String(++sequence).padStart(12, "0")}`;
    const payload = `v1.${asset.replaceAll("-", "")}.${delivery.replaceAll("-", "")}.${Math.floor(Date.now() / 1000)}`;
    const receipt = `${payload}.${createHmac("sha256", signingKey).update(payload).digest("hex")}`;
    receipts.add(receipt);
    return {
      product_id: productIds[kind],
      asset_id: asset,
      campaign_id: `93333333-3333-4333-8333-${String(index).padStart(12, "0")}`,
      name: {
        shared: "LaunchPad developer tools",
        source: "BuildScope engineering insights",
        topic: "TypeScript validation toolkit",
        tag: "Engineering release assistant",
      }[kind],
      tagline: "Practical tools for shipping dependable applications with your team.",
      url: `https://columns-sponsor.example.test/${kind}`,
      logo_url: "https://columns-sponsor.example.test/logo.svg",
      image_url: "https://columns-sponsor.example.test/creative.svg",
      image_variants: [],
      link_rel: ["nofollow"],
      partner_name: "Developer Tools Partner",
      platform: { name: "Developer LaunchPad", logo_url: null, logo_variants: [] },
      attribution: "Brought to you by Developer Tools Partner",
      sponsored: true,
      reason: "An eligible developer product matching this feed's public article context.",
      receipt,
      ad_format: "feed",
      article: null,
    };
  };
  const queryPattern = /\/api\/v1\/(?:user\/)?partner-placements\/query$/;
  const queryHandler = async (route) => {
    const request = route.request();
    assert.equal(request.method(), "POST");
    const body = request.postDataJSON();
    const headers = await request.allHeaders();
    assert.ok(new URL(request.url()).pathname.includes("/user/"), "Member delivery stays private");
    assert.ok(headers["x-csrf-token"], "Private delivery retains CSRF protection");
    assert.equal(headers.authorization, undefined);
    assert.ok(
      Object.keys(body).every((key) =>
        [
          "surface",
          "article_id",
          "topic_id",
          "article_ids",
          "search",
          "allow_sponsored_fallback",
          "exclude_product_ids",
          "ad_formats",
        ].includes(key),
      ),
      "Delivery sends public context, never account attributes",
    );
    if (!["feed", "topic"].includes(body.surface)) return route.fulfill({ json: { items: [] } });
    const kind = kindFor(body);
    const selected = [product(["latest", "recommended"].includes(kind) ? "shared" : kind)].filter(
      (item) => !body.exclude_product_ids?.includes(item.product_id),
    );
    const record = { kind, body, items: selected, started: Date.now() };
    queries.push(record);
    // A small delay makes independently admitted ad requests overlap observably.
    await new Promise((resolve) => setTimeout(resolve, 120));
    await route.fulfill({ json: { items: selected } });
    record.finished = Date.now();
  };
  const trackingPattern = /\/api\/v1\/partner-tracking\/events$/;
  const trackingHandler = async (route) => {
    const request = route.request();
    const headers = await request.allHeaders();
    assert.equal(request.method(), "POST");
    assert.equal(headers.cookie, undefined, "Signed tracking omits account cookies");
    assert.equal(headers.authorization, undefined);
    assert.equal(headers["x-csrf-token"], undefined);
    const body = request.postDataJSON();
    assert.deepEqual(Object.keys(body).sort(), ["kind", "receipt"]);
    assert.ok(["impression", "click"].includes(body.kind));
    assert.ok(receipts.has(body.receipt), "Tracking references an issued delivery");
    const parts = /^(v1\.[\da-f]{32}\.[\da-f]{32}\.\d+)\.([\da-f]{64})$/.exec(body.receipt);
    assert.ok(parts);
    assert.equal(parts[2], createHmac("sha256", signingKey).update(parts[1]).digest("hex"));
    events.push(body);
    return route.fulfill({ status: 204 });
  };
  const imagePattern = "https://columns-sponsor.example.test/*.svg";
  const imageHandler = (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="#00786f"/><text x="400" y="240" text-anchor="middle" fill="white" font-family="sans-serif" font-size="48">Developer tools</text></svg>',
    });
  for (const [pattern, handler] of [
    [queryPattern, queryHandler],
    [trackingPattern, trackingHandler],
    [imagePattern, imageHandler],
  ])
    await context.route(pattern, handler);
  return {
    queries,
    events,
    cleanup: async () => {
      for (const [pattern, handler] of [
        [queryPattern, queryHandler],
        [trackingPattern, trackingHandler],
        [imagePattern, imageHandler],
      ])
        await context.unroute(pattern, handler);
    },
  };
}

/** Columns and the existing article layouts retain contextual paid delivery and tracking. */
export async function checkColumnsSponsoredProducts(page, target, output) {
  const fixture = await installColumnsFeedFixture(page, { signedIn: true });
  fixture.setStress(true);
  fixture.setOrdinaryArticles(
    Array.from({ length: 24 }, (_, index) =>
      columnsFixtureArticle(`columns-latest-0-${index + 2}`),
    ),
  );
  const ads = await installSponsoredProducts(page);
  const context = page.context();
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const organic = ".article-stacked-row:not(.partner-placement)";
  const placements = workspace.locator(".partner-placement--column");
  const column = (index) => columns.nth(index);
  const source = column(1);
  const topic = column(2);
  const tag = column(3);
  const queue = { active: new Set(), maximum: 0, requests: [] };
  let measureQueue = true;
  const requestStart = (request) => {
    if (!measureQueue) return;
    const url = new URL(request.url());
    const columnFeed =
      /\/api\/v1\/(?:user\/)?feed$/.test(url.pathname) &&
      url.searchParams.get("limit") === "24" &&
      !url.search.endsWith("&") &&
      !url.searchParams.has("generation");
    const columnDetail = /\/api\/v1\/(topics|sources|tags)\/[\da-f-]{36}$/.test(url.pathname);
    const columnAd =
      url.pathname.endsWith("/partner-placements/query") &&
      ["feed", "topic"].includes(request.postDataJSON().surface);
    if (!columnFeed && !columnDetail && !columnAd) return;
    queue.active.add(request);
    queue.maximum = Math.max(queue.maximum, queue.active.size);
    queue.requests.push({ url: url.href, active: queue.active.size });
  };
  const requestEnd = (request) => queue.active.delete(request);
  page.on("request", requestStart);
  page.on("requestfinished", requestEnd);
  page.on("requestfailed", requestEnd);
  const count = (kind, receipt) =>
    ads.events.filter((event) => event.kind === kind && event.receipt === receipt).length;
  const proof = {
    queue: null,
    preview: null,
    layouts: [],
    queries: ads.queries,
    events: ads.events,
  };
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(target);
    await workspace.waitFor();
    await waitFor(async () => (await columns.count()) === 5, "All five saved columns mount");
    await waitFor(
      async () => (await workspace.locator(organic).count()) === 120,
      "Five independent feeds each load their initial 24 articles",
    );
    await waitFor(
      () => ads.queries.length === 5 && ads.queries.every((query) => query.finished),
      "Every column receives a bounded delivery",
    );
    await waitFor(
      async () => (await placements.count()) === 4,
      "Contextual products and one globally deduplicated main-feed product render",
    );
    await waitFor(() => queue.active.size === 0, "Column read transports settle");
    assert.ok(
      queue.maximum <= 3,
      `Feeds, selection details, and ads share the queue (${queue.maximum})`,
    );
    assert.ok(queue.maximum >= 2, "The fixture observes concurrent requests");
    proof.queue = { maximum: queue.maximum, requests: queue.requests };
    measureQueue = false;

    for (const query of ads.queries) {
      assert.deepEqual(query.body.ad_formats, ["feed"], "Columns request product ads only");
      if (query.kind === "topic") {
        assert.equal(query.body.surface, "topic");
        assert.equal(query.body.topic_id, columnsCatalog.topics[0].id);
        assert.equal(query.body.article_ids, undefined);
        assert.notEqual(query.body.allow_sponsored_fallback, true);
      } else {
        assert.equal(query.body.surface, "feed");
        assert.equal(query.body.article_ids.length, 24);
        assert.equal(new Set(query.body.article_ids).size, 24);
        assert.ok(query.body.article_ids.every((id) => uuid.test(id)));
        assert.equal(
          query.body.allow_sponsored_fallback,
          ["latest", "recommended"].includes(query.kind),
        );
      }
    }
    for (const [container, kind] of [
      [source, "source"],
      [topic, "topic"],
      [tag, "tag"],
    ])
      assert.equal(
        await container.locator(`[data-product-id="${productIds[kind]}"]`).count(),
        1,
        `${kind} eligibility renders in its own column`,
      );
    const ids = await placements.evaluateAll((nodes) =>
      nodes.map((node) => node.dataset.productId),
    );
    assert.equal(
      new Set(ids).size,
      ids.length,
      "The same product is never shown in multiple columns",
    );
    assert.equal(ids.filter((id) => id === productIds.shared).length, 1);
    assert.equal(
      await workspace.locator("img").count(),
      0,
      "Columns keep product and article rows text-only despite available creative images",
    );
    for (const placement of await placements.all()) {
      assert.equal(await placement.locator('[data-promotion-kind="sponsored"]').count(), 1);
      assert.equal(
        await placement.evaluate((node) => {
          let articles = 0;
          for (let item = node.previousElementSibling; item; item = item.previousElementSibling)
            if (item.matches(".article-stacked-row:not(.partner-placement)")) articles++;
          return articles;
        }),
        4,
        "Each promotion follows the fourth organic article",
      );
    }

    const sourceAd = source.locator(`[data-product-id="${productIds.source}"]`);
    const link = sourceAd.locator(".partner-placement-link");
    const sourceDelivery = ads.queries.find((query) => query.kind === "source").items[0];
    assert.equal(await link.getAttribute("href"), sourceDelivery.url);
    assert.equal(await link.getAttribute("target"), "_blank");
    for (const token of ["nofollow", "noopener", "noreferrer"])
      assert.ok((await link.getAttribute("rel")).split(/\s+/).includes(token));
    await link.scrollIntoViewIfNeeded();
    const beforeHover = await sourceAd.boundingBox();
    await link.hover();
    await waitFor(
      () =>
        sourceAd.evaluate((node) => {
          const probe = document.createElement("span");
          probe.style.color = "var(--sponsored-border)";
          node.append(probe);
          const expected = getComputedStyle(probe).color;
          probe.remove();
          return getComputedStyle(node).borderBottomColor === expected;
        }),
      "Hover visibly highlights the paid product border",
    );
    const hovered = await sourceAd.boundingBox();
    assert.ok(
      Math.abs(hovered.width - beforeHover.width) < 1 &&
        Math.abs(hovered.height - beforeHover.height) < 1,
      "Hover preserves article and promotion geometry",
    );
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await waitFor(
      () => count("impression", sourceDelivery.receipt) === 1,
      "A visible sponsored product reports its signed impression",
    );
    await link.evaluate((node) =>
      node.addEventListener("click", (event) => event.preventDefault(), { once: true }),
    );
    await page.keyboard.press("Tab");
    await link.focus();
    assert.equal(await link.evaluate((node) => node.matches(":focus-visible")), true);
    const focus = await link.evaluate((node) => ({
      width: parseFloat(getComputedStyle(node).outlineWidth),
      style: getComputedStyle(node).outlineStyle,
    }));
    assert.ok(
      focus.width >= 2 && focus.style !== "none",
      "Keyboard focus remains visible inside the column",
    );
    proof.interaction = { beforeHover, hovered, focus };
    await page.keyboard.press("Enter");
    await waitFor(
      () => count("click", sourceDelivery.receipt) === 1,
      "A trusted keyboard click reports its signed receipt",
    );
    await page.screenshot({ path: `${output}-desktop-focus.png`, animations: "disabled" });
    await page.mouse.move(0, 0);
    await link.evaluate((node) => node.blur());
    await page.screenshot({ path: `${output}-desktop.png`, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    await source.evaluate((node) =>
      node.scrollIntoView({ inline: "start", block: "nearest", behavior: "instant" }),
    );
    await link.scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: `${output}-mobile.png`, animations: "disabled" });
    await page.setViewportSize({ width: 1440, height: 1000 });

    const title = column(0).locator(".article-stacked-title").first();
    await title.scrollIntoViewIfNeeded();
    const row = await column(0).locator(organic).first().elementHandle();
    const promoted = await sourceAd.elementHandle();
    const before = {
      feeds: fixture.requests.length,
      deliveries: ads.queries.length,
      writes: fixture.scopedViews.writes.length,
    };
    const returnUrl = page.url();
    try {
      await title.click();
      const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
      await preview.waitFor();
      assert.equal(await row.evaluate((node) => node.isConnected), true);
      assert.equal(await promoted.evaluate((node) => node.isConnected), true);
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await preview.waitFor({ state: "hidden" });
      await page.waitForURL(returnUrl);
      assert.equal(await row.evaluate((node) => node.isConnected), true);
      assert.equal(await promoted.evaluate((node) => node.isConnected), true);
      assert.deepEqual(
        {
          feeds: fixture.requests.length,
          deliveries: ads.queries.length,
          writes: fixture.scopedViews.writes.length,
        },
        before,
        "Opening and closing a preview preserves articles and paid delivery",
      );
      proof.preview = { retainedArticle: true, retainedProduct: true, before, returnUrl };
    } finally {
      await row.dispose();
      await promoted.dispose();
    }
    await source.getByRole("button", { name: "Load more articles", exact: true }).click();
    await waitFor(
      async () => (await source.locator(organic).count()) === 48,
      "Only the requested column advances its article cursor",
    );
    assert.equal(
      ads.queries.length,
      before.deliveries,
      "Pagination retains the first-page placement context",
    );
    assert.equal(await placements.count(), 4);
    await link.scrollIntoViewIfNeeded();
    await new Promise((resolve) => setTimeout(resolve, 1200));
    assert.equal(
      count("impression", sourceDelivery.receipt),
      1,
      "Pagination and preview restoration never repeat an acknowledged impression",
    );

    for (const [view, selector] of [
      ["cards", ".article-grid > .partner-placement"],
      ["compact", ".article-table .partner-placement--row"],
      ["stacked", ".article-stacked > .partner-placement--stacked"],
    ]) {
      await chooseInlineArticleView(page, view);
      await workspace.waitFor({ state: "hidden" });
      const product = page.locator(selector).first();
      await product.waitFor();
      assert.equal(await product.getAttribute("data-product-id"), productIds.shared);
      assert.ok((await product.locator('[data-promotion-kind="sponsored"]').count()) >= 1);
      const delivery = ads.queries.at(-1);
      assert.equal(delivery.body.surface, "feed");
      assert.equal(delivery.body.allow_sponsored_fallback, true);
      assert.equal(delivery.body.article_ids.length, 24);
      assert.deepEqual(
        delivery.body.ad_formats,
        view === "compact" ? ["feed"] : ["feed", "sponsored_content"],
      );
      proof.layouts.push({ view, product: productIds.shared, query: delivery.body });
    }
    await writeFile(`${output}.json`, `${JSON.stringify(proof, null, 2)}\n`);
  } catch (error) {
    await page.screenshot({ path: `${output}-failure.png`, animations: "disabled" });
    await writeFile(
      `${output}-failure.json`,
      `${JSON.stringify({ ...proof, queue: { maximum: queue.maximum, requests: queue.requests } }, null, 2)}\n`,
    );
    throw error;
  } finally {
    page.off("request", requestStart);
    page.off("requestfinished", requestEnd);
    page.off("requestfailed", requestEnd);
    await ads.cleanup();
    await fixture.cleanup();
  }
}
