import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { chromium } from "playwright";

// Exercise actual production reader bundles and packaged extensions, with controlled
// delivery responses. Backend integration tests prove relevance and signed receipts.
const topic = {
  id: "66666666-6666-4666-8666-666666666666",
  slug: "typescript",
  name: "TypeScript",
  kind: "language",
  description: "Typed JavaScript",
  ai_description: null,
  logo_url: null,
};
const source = {
  id: "77777777-7777-4777-8777-777777777777",
  slug: "engineering",
  name: "Engineering Journal",
  website_url: "https://publisher.test",
  logo_url: null,
};
const article = {
  id: "88888888-8888-4888-8888-888888888888",
  slug: "validate-typescript-inputs",
  title: "Validate TypeScript inputs",
  canonical_url: "https://publisher.test/typescript",
  summary: "A practical tutorial about validating TypeScript application inputs.",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: null,
  content_type: "tutorial",
  content_format: "article",
  language: "en",
  published_at: "2026-10-10T10:00:00Z",
  feed_at: "2026-10-10T10:00:00Z",
  tags: [],
  topics: [{ ...topic, role: "primary" }],
  sources: [source],
};
const ordinary = {
  product_id: "11111111-1111-4111-8111-111111111111",
  asset_id: "22222222-2222-4222-8222-222222222222",
  campaign_id: null,
  name: "TypeScript validator",
  description: "Validate application inputs.",
  url: "https://partner.example/validator",
  logo_url: "https://assets.partner.example/validator.svg",
  image_url: "https://assets.partner.example/og/validator-original.svg",
  image_variants: [
    { url: "https://assets.partner.example/og/validator-400.svg", width: 400 },
    { url: "https://assets.partner.example/og/validator-800.svg", width: 800 },
  ],
  partner_name: "Free partner",
  sponsored: false,
  reason: "Supports the TypeScript validation task in this article.",
  receipt: "ordinary-delivery",
};
const sponsored = {
  ...ordinary,
  product_id: "33333333-3333-4333-8333-333333333333",
  asset_id: "44444444-4444-4444-8444-444444444444",
  campaign_id: "55555555-5555-4555-8555-555555555555",
  name: "Sponsored validator",
  partner_name: "Paid partner",
  sponsored: true,
  receipt: "sponsored-delivery",
  logo_url: "https://assets.partner.example/broken.png",
  image_url: "https://assets.partner.example/og/broken.png",
  image_variants: [],
};
const news = {
  ...article,
  id: "99999999-9999-4999-8999-999999999999",
  slug: "unrelated-news",
  title: "Unrelated industry news",
  content_type: "news",
};
const feedArticles = Array.from({ length: 24 }, (_, index) =>
  index === 0
    ? article
    : {
        ...article,
        id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
        slug: `typescript-tutorial-${index}`,
        title: `TypeScript tutorial ${index}`,
      },
);
const temp = await mkdtemp(path.join(tmpdir(), "devfeed-placement-browser-"));
await mkdir("reports/partner-placements", { recursive: true });
let signedIn = false;
let queries = [];
let events = [];
let deliveryGate = null;
let mediaMode = "standard";

function productMedia(item) {
  if (item.product_id !== ordinary.product_id || mediaMode === "standard") return item;
  return {
    ...item,
    image_url: mediaMode === "broken-og" ? sponsored.image_url : null,
    image_variants: [],
    logo_url: mediaMode === "no-assets" ? null : ordinary.logo_url,
  };
}

async function waitForVisibleProductLogos(page) {
  const dismissInvitation = page.getByRole("button", { name: "Not now", exact: true });
  if (await dismissInvitation.isVisible()) await dismissInvitation.click();
  await page.waitForFunction(() => {
    const visible = Array.from(document.querySelectorAll(".partner-placement")).filter((card) => {
      const rect = card.getBoundingClientRect();
      return (
        rect.bottom > 0 && rect.top < innerHeight && getComputedStyle(card).visibility !== "hidden"
      );
    });
    return (
      visible.length > 0 &&
      visible.every((card) => {
        const media =
          card.querySelector(".card-image") ?? card.querySelector(".partner-placement-logo");
        const image = media?.querySelector("img");
        return image ? image.complete && image.naturalWidth > 0 : !!media?.textContent.trim();
      })
    );
  });
}

async function assertSponsoredPill(page, selector, label, articleBadge) {
  const badges = page.locator(selector);
  assert.ok(await badges.count(), `${label}: Sponsored badges are present`);
  for (const badge of await badges.all())
    await assertSingleSponsoredPill(page, badge, label, articleBadge);
}

async function assertSingleSponsoredPill(page, badge, label, articleBadge) {
  const treatment = await badge.evaluate((element) => {
    const style = getComputedStyle(element);
    const footer = element
      .closest(".partner-placement")
      .querySelector(".partner-placement-destination");
    const rect = element.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(element);
    const text = range.getBoundingClientRect();
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d");
    const rgba = (color) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return Array.from(context.getImageData(0, 0, 1, 1).data);
    };
    const luminance = (color) => {
      const rgb = rgba(color);
      const linear = Array.from(rgb)
        .slice(0, 3)
        .map((channel) => {
          const value = channel / 255;
          return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    };
    const foreground = luminance(style.color);
    const background = luminance(style.backgroundColor);
    return {
      destination: {
        text: footer.textContent.trim(),
        icons: footer.querySelectorAll("svg").length,
        withinCardLink: !!footer.closest("a.partner-placement-link[href]"),
      },
      geometry: {
        width: rect.width,
        height: rect.height,
        textWidth: text.width,
        textHeight: text.height,
      },
      shared: Object.fromEntries(
        [
          "borderRadius",
          "borderTopWidth",
          "borderTopStyle",
          "paddingTop",
          "paddingRight",
          "paddingBottom",
          "paddingLeft",
          "fontSize",
          "lineHeight",
          "letterSpacing",
          "textTransform",
        ].map((property) => [property, style[property]]),
      ),
      background: style.backgroundColor,
      fontWeight: style.fontWeight,
      fill: rgba(style.backgroundColor),
      borderColor: style.borderTopColor,
      whiteSpace: style.whiteSpace,
      contrast:
        (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05),
    };
  });
  assert.equal(await badge.textContent(), "Sponsored", `${label}: explicit product disclosure`);
  assert.equal(
    treatment.destination.text,
    "partner.example",
    `${label}: only the destination domain remains`,
  );
  assert.equal(treatment.destination.icons, 0, `${label}: footer has no arrow`);
  assert.ok(treatment.destination.withinCardLink, `${label}: whole product card remains linked`);
  assert.ok(parseFloat(treatment.shared.borderRadius) > 0, `${label}: rounded badge`);
  assert.ok(parseFloat(treatment.shared.borderTopWidth) > 0, `${label}: visible badge border`);
  assert.equal(treatment.shared.borderTopStyle, "solid", `${label}: solid badge outline`);
  assert.ok(parseFloat(treatment.shared.paddingLeft) > 0, `${label}: horizontal badge padding`);
  assert.ok(parseFloat(treatment.shared.paddingTop) > 0, `${label}: vertical badge padding`);
  assert.ok(
    !["transparent", "rgba(0, 0, 0, 0)"].includes(treatment.background),
    `${label}: filled badge`,
  );
  assert.ok(
    !["transparent", "rgba(0, 0, 0, 0)"].includes(treatment.borderColor),
    `${label}: colored outline`,
  );
  assert.equal(treatment.fill[3], 255, `${label}: opaque Sponsored fill`);
  assert.ok(Number(treatment.fontWeight) >= 700, `${label}: strong Sponsored disclosure`);
  assert.ok(
    treatment.fill[0] > treatment.fill[1] && treatment.fill[1] > treatment.fill[2] + 60,
    `${label}: distinctly amber Sponsored color`,
  );
  assert.equal(treatment.whiteSpace, "nowrap", `${label}: one-line disclosure`);
  assert.ok(treatment.contrast >= 4.5, `${label}: readable badge contrast (${treatment.contrast})`);
  assert.ok(treatment.geometry.height <= 28, `${label}: compact badge height`);
  assert.ok(
    treatment.geometry.width >=
      treatment.geometry.textWidth + parseFloat(treatment.shared.paddingLeft) * 2,
    `${label}: disclosure fits within its pill`,
  );
  if (articleBadge) {
    const reference = await page
      .locator(articleBadge)
      .first()
      .evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          background: style.backgroundColor,
          shared: Object.fromEntries(
            [
              "borderRadius",
              "borderTopWidth",
              "borderTopStyle",
              "paddingTop",
              "paddingRight",
              "paddingBottom",
              "paddingLeft",
              "fontSize",
              "lineHeight",
              "letterSpacing",
              "textTransform",
            ].map((property) => [property, style[property]]),
          ),
        };
      });
    assert.deepEqual(
      treatment.shared,
      reference.shared,
      `${label}: matches the article badge treatment`,
    );
    assert.notEqual(
      treatment.background,
      reference.background,
      `${label}: Sponsored has its own color`,
    );
  }
  return treatment;
}

function response(requestPath, body, headers = {}, extension = false) {
  const url = new URL(requestPath, "https://devfeed.tech");
  const pathname = url.pathname.replace(/^\/api/, "");
  if (["/v1/partner-placements/query", "/v1/user/partner-placements/query"].includes(pathname)) {
    assert.equal(headers.authorization, undefined);
    if (pathname === "/v1/partner-placements/query") {
      assert.equal(headers.cookie, undefined);
      assert.equal(headers["x-csrf-token"], undefined);
    } else assert.equal(headers["x-csrf-token"], "fixture-csrf");
    queries.push({ pathname, body });
    const eligible =
      body.surface === "topic" ||
      pathname.includes("/user/") ||
      (body.surface === "feed" && body.article_ids?.includes(article.id)) ||
      (body.surface === "article" && feedArticles.some((item) => item.id === body.article_id));
    return {
      items: eligible
        ? [ordinary, sponsored]
            .filter((item) => !body.exclude_product_ids?.includes(item.product_id))
            .map(productMedia)
        : [],
    };
  }
  if (pathname === "/v1/partner-tracking/events") {
    assert.equal(headers.cookie, undefined);
    assert.equal(headers.authorization, undefined);
    assert.deepEqual(Object.keys(body).sort(), ["kind", "receipt"]);
    assert.ok([ordinary.receipt, sponsored.receipt].includes(body.receipt));
    events.push(body);
    return null;
  }
  if (pathname === "/v1/user/auth/me")
    return signedIn
      ? {
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          name: "Reader",
          email: null,
          expires_at: 9999999999,
          csrf_token: "fixture-csrf",
        }
      : null;
  if (pathname === "/v1/user/auth/config") return { enabled: true, providers: [] };
  if (pathname === "/v1/user/settings/profile")
    return {
      display_name: "Reader",
      avatar_url: null,
      username: "fixture-reader",
      links: [],
      stack: [],
    };
  if (pathname === "/v1/user/settings/feed")
    return { view: "cards", content_types: ["tutorial", "news"], languages: ["en"] };
  if (pathname === "/v1/user/preferences/topics") return { topic_ids: [topic.id] };
  if (pathname === "/v1/user/preferences/sources") return { source_ids: [] };
  if (pathname === "/v1/user/engagement") return { items: [] };
  if (pathname === "/v1/user/feed")
    return {
      items: feedArticles,
      next_cursor: null,
      status: "ready",
      has_interests: true,
      reasons: {},
      feed_kind: "personalized",
      refresh_state: "idle",
    };
  if (pathname === "/v1/feed") return { items: feedArticles, next_cursor: null };
  if (pathname === "/v1/search") {
    const sections = {
      articles: {
        items: feedArticles.map((item) => ({
          id: item.id,
          title: item.title,
          description: item.summary,
          href: `/articles/${item.slug}`,
          image_url: null,
          label: item.content_type,
          published_at: item.published_at,
          click_token: "fixture-result-token",
        })),
        next_cursor: null,
      },
      topics: {
        items: [
          {
            id: topic.id,
            title: topic.name,
            description: topic.description,
            href: `/topics/${topic.slug}`,
            image_url: null,
            label: "Topic",
            published_at: null,
          },
        ],
        next_cursor: null,
      },
      sources: {
        items: [
          {
            id: source.id,
            title: source.name,
            description: "Engineering articles",
            href: `/sources/${source.slug}`,
            image_url: null,
            label: "Source",
            published_at: null,
          },
        ],
        next_cursor: null,
      },
      tags: {
        items: [
          {
            id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            title: "typescript",
            description: "",
            href: "/tags/typescript",
            image_url: null,
            label: "Tag",
            published_at: null,
          },
        ],
        next_cursor: null,
      },
    };
    const section = url.searchParams.get("section");
    return {
      query: url.searchParams.get("q") ?? "",
      sections: section ? { [section]: sections[section] } : sections,
    };
  }
  if (pathname === "/v1/feed/options")
    return { sources: [source], content_types: ["tutorial", "news"] };
  if (pathname === "/v1/topics/typescript") return topic;
  if (pathname === "/v1/topics") return extension ? { items: [topic], next_offset: null } : [topic];
  if (pathname === "/v1/sources")
    return extension ? { items: [source], next_offset: null } : [source];
  if (pathname.startsWith("/v1/articles/")) {
    const selected = pathname.endsWith(news.slug)
      ? news
      : (feedArticles.find((item) => pathname.endsWith(item.slug)) ?? article);
    return extension ? { article: selected, topic } : selected;
  }
  return {};
}

const fixture = createServer(async (req, res) => {
  try {
    let raw = "";
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : undefined;
    if (body?.surface === "feed" && deliveryGate) await deliveryGate;
    const json = response(req.url, body, req.headers);
    const noContent = new URL(req.url, "http://fixture").pathname === "/v1/partner-tracking/events";
    res.writeHead(noContent ? 204 : 200, { "Content-Type": "application/json" });
    res.end(noContent ? undefined : JSON.stringify(json));
  } catch (error) {
    console.error(error);
    res.writeHead(500);
    res.end();
  }
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
    path.resolve("node_modules/next/dist/bin/next"),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: path.resolve("apps/web"),
    env: {
      ...env,
      DEVFEED_PUBLIC_API_URL: upstream,
      DEVFEED_USER_API_URL: upstream,
      DEVFEED_USER_BASE_URL: origin,
      DEVFEED_ANALYTICS_ENABLED: "false",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (data) => {
  logs += data;
});
app.stderr.on("data", (data) => {
  logs += data;
});
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      await fetch(`${origin}/login`);
      ready = true;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  assert.ok(ready, logs);
  for (const platform of ["web", "chrome", "edge"]) {
    signedIn = false;
    queries = [];
    events = [];
    mediaMode = "standard";
    const extension = platform === "web" ? null : path.resolve(`apps/extensions/dist/${platform}`);
    const context = await chromium.launchPersistentContext(path.join(temp, platform), {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        ? undefined
        : platform === "edge"
          ? "msedge"
          : "chromium",
      headless: true,
      viewport: { width: 1280, height: 900 },
      args: extension
        ? [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
        : [],
    });
    try {
      let base = origin;
      if (extension) {
        const manifest = JSON.parse(await readFile(path.join(extension, "manifest.json"), "utf8"));
        const id = createHash("sha256")
          .update(Buffer.from(manifest.key, "base64"))
          .digest("hex")
          .slice(0, 32)
          .replace(/[0-9a-f]/g, (digit) => String.fromCharCode(97 + parseInt(digit, 16)));
        base = `chrome-extension://${id}/newtab.html#`;
        await context.route("https://devfeed.tech/api/**", async (route) => {
          const request = route.request();
          const raw = request.postData();
          const body = raw ? JSON.parse(raw) : undefined;
          if (body?.surface === "feed" && deliveryGate) await deliveryGate;
          const json = response(request.url(), body, request.headers(), true);
          await route.fulfill(
            request.url().endsWith("/partner-tracking/events") ? { status: 204 } : { json },
          );
        });
      }
      await context.route("https://assets.partner.example/**", (route) =>
        route.request().url().endsWith("broken.png")
          ? route.fulfill({ status: 404, body: "Missing" })
          : route.fulfill({
              contentType: "image/svg+xml",
              body: route.request().url().includes("/og/")
                ? '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><defs><linearGradient id="bg"><stop stop-color="#122b5c"/><stop offset="1" stop-color="#3178c6"/></linearGradient></defs><rect width="1200" height="675" fill="url(#bg)"/><text x="100" y="270" fill="white" font-size="68" font-family="sans-serif">TypeScript Validator</text><text x="100" y="390" fill="#a5d8ff" font-size="42" font-family="sans-serif">Safe inputs. Confident applications.</text></svg>'
                : '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" rx="10" fill="#3178c6"/><text x="8" y="33" fill="white" font-size="24">TS</text></svg>',
            }),
      );
      await context.route("https://partner.example/**", (route) =>
        route.fulfill({ contentType: "text/html", body: "<p>Partner destination</p>" }),
      );
      await context.addCookies([
        {
          name: "devfeed_user_session",
          value: "private-fixture",
          url: extension ? "https://devfeed.tech" : origin,
          httpOnly: true,
        },
      ]);
      await context.addInitScript(() => localStorage.setItem("devfeed:dev-card-promo-seen", "1"));
      const page = await context.newPage();
      await page.goto(`${base}/articles/${article.slug}`);
      const free = page.getByRole("link", { name: ordinary.name, exact: true });
      await free.waitFor();
      await page.getByRole("link", { name: sponsored.name, exact: true }).waitFor();
      await assertSponsoredPill(
        page,
        ".preview-sidebar .partner-placement-label",
        `${platform}: desktop preview`,
      );
      assert.equal(
        await page
          .locator(".partner-placement-label")
          .allTextContents()
          .then((labels) => labels.sort())
          .then(JSON.stringify),
        JSON.stringify(["Sponsored", "Sponsored"]),
      );
      assert.equal(
        await page.getByRole("link", { name: sponsored.name, exact: true }).getAttribute("rel"),
        "sponsored noopener noreferrer",
      );
      assert.equal(
        await page.getByRole("button", { name: `Hide ${sponsored.name}`, exact: true }).count(),
        0,
        `${platform}: sponsored preview product has no dismissal control`,
      );
      assert.equal(
        await page.getByRole("button", { name: `Hide ${ordinary.name}`, exact: true }).count(),
        0,
        `${platform}: non-campaign preview product has no dismissal control`,
      );
      assert.equal(await page.locator(".partner-placement button").count(), 0);
      for (const item of [ordinary, sponsored]) {
        const link = page.getByRole("link", { name: item.name, exact: true });
        assert.equal(await link.getAttribute("rel"), "sponsored noopener noreferrer");
        assert.equal(await link.getAttribute("href"), item.url);
        assert.equal(
          await link.evaluate(
            (element) =>
              document.getElementById(element.getAttribute("aria-describedby"))?.textContent,
          ),
          "Sponsored",
        );
      }
      assert.equal(await page.getByText("Relevant product", { exact: true }).count(), 0);
      assert.equal(await page.getByText(ordinary.reason, { exact: true }).count(), 0);
      assert.equal(await page.locator(".preview-copy .partner-placement").count(), 0);
      assert.equal(await page.locator(".preview-sidebar .partner-placement").count(), 2);
      const successfulLogo = page.locator(
        `.partner-placement[data-product-id="${ordinary.product_id}"] img`,
      );
      await successfulLogo.waitFor();
      await successfulLogo.scrollIntoViewIfNeeded();
      await page.waitForFunction((id) => {
        const image = document.querySelector(`.partner-placement[data-product-id="${id}"] img`);
        return image?.complete && image.naturalWidth > 0;
      }, ordinary.product_id);
      const fallback = page.locator(
        `.partner-placement[data-product-id="${sponsored.product_id}"] .partner-placement-logo`,
      );
      await fallback.scrollIntoViewIfNeeded();
      await page.waitForFunction((id) => {
        const logo = document.querySelector(
          `.partner-placement[data-product-id="${id}"] .partner-placement-logo`,
        );
        return !!logo?.textContent.trim() && !logo.querySelector("img");
      }, sponsored.product_id);
      await free.scrollIntoViewIfNeeded();
      await page.waitForFunction(() =>
        JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1"))?.entries.some(
          (entry) => entry.count > 0,
        ),
      );
      const popupEvent = context.waitForEvent("page");
      await free.click();
      const popup = await popupEvent;
      await popup.close();
      await page.waitForTimeout(250);
      assert.ok(
        events.some((event) => event.kind === "impression" && event.receipt === ordinary.receipt),
        `${platform}: viewable signed impression`,
      );
      assert.ok(
        events.some((event) => event.kind === "click" && event.receipt === ordinary.receipt),
        `${platform}: independent signed click`,
      );
      await page.mouse.move(20, 20);
      await waitForVisibleProductLogos(page);
      await page.screenshot({ path: `reports/partner-placements/${platform}-article-desktop.png` });
      await page.getByRole("link", { name: sponsored.name, exact: true }).scrollIntoViewIfNeeded();
      await page.waitForFunction(
        (id) =>
          JSON.parse(
            localStorage.getItem("devfeed:partner-placement-preferences:v1"),
          )?.entries.some((entry) => entry.id === id && entry.count > 0),
        sponsored.product_id,
      );
      const sponsoredPopupEvent = context.waitForEvent("page");
      await page.getByRole("link", { name: sponsored.name, exact: true }).click();
      const sponsoredPopup = await sponsoredPopupEvent;
      await sponsoredPopup.close();
      await page.waitForTimeout(250);
      assert.ok(
        events.some((event) => event.kind === "impression" && event.receipt === sponsored.receipt),
        `${platform}: non-dismissible sponsored product retains signed impression tracking`,
      );
      assert.ok(
        events.some((event) => event.kind === "click" && event.receipt === sponsored.receipt),
        `${platform}: non-dismissible sponsored product retains signed click tracking`,
      );
      await page.reload();
      await page.getByRole("heading", { name: article.title, level: 1, exact: true }).waitFor();
      await page.getByRole("link", { name: sponsored.name, exact: true }).waitFor();
      await page.getByRole("link", { name: ordinary.name, exact: true }).waitFor();
      const articleQuery = queries.filter((query) => query.body.surface === "article").at(-1);
      assert.deepEqual(articleQuery.body.exclude_product_ids, []);
      for (const age of [0, 86_400_000, 91 * 86_400_000]) {
        await page.evaluate(
          ({ products, age }) => {
            const viewed = Date.now() - age;
            localStorage.setItem(
              "devfeed:partner-placement-preferences:v1",
              JSON.stringify({
                version: 1,
                entries: products.map((id) => ({
                  id,
                  day: new Date(viewed).toISOString().slice(0, 10),
                  count: 50,
                  cap: 1,
                  seen: viewed,
                  hidden: Date.now() + 90 * 86_400_000,
                })),
              }),
            );
          },
          { products: [ordinary.product_id, sponsored.product_id], age },
        );
        await page.reload();
        await page.getByRole("link", { name: ordinary.name, exact: true }).waitFor();
        await page.getByRole("link", { name: sponsored.name, exact: true }).waitFor();
        const repeated = queries.filter((query) => query.body.surface === "article").at(-1);
        assert.deepEqual(
          repeated.body.exclude_product_ids,
          [],
          `${platform}: legacy hides, limits and ${age}ms history do not suppress delivery`,
        );
        assert.equal(await page.locator(".preview-sidebar .partner-placement").count(), 2);
      }
      await page.addInitScript(() => {
        const get = Storage.prototype.getItem;
        const set = Storage.prototype.setItem;
        Storage.prototype.getItem = function (key) {
          if (
            key === "devfeed:partner-placement-preferences:v1" &&
            get.call(sessionStorage, "test:placement-storage") === "unreadable"
          )
            throw new DOMException("Storage blocked", "SecurityError");
          return get.call(this, key);
        };
        Storage.prototype.setItem = function (key, value) {
          if (
            key === "devfeed:partner-placement-preferences:v1" &&
            get.call(sessionStorage, "test:placement-storage") === "unwritable"
          )
            throw new DOMException("Storage blocked", "QuotaExceededError");
          return set.call(this, key, value);
        };
      });
      for (const failure of ["malformed", "unreadable", "unwritable"]) {
        await page.evaluate((failure) => {
          sessionStorage.setItem("test:placement-storage", failure);
          localStorage.removeItem("devfeed:partner-placement-preferences:v1");
          if (failure === "malformed")
            localStorage.setItem("devfeed:partner-placement-preferences:v1", "{bad");
        }, failure);
        await page.reload();
        await page.getByRole("link", { name: ordinary.name, exact: true }).waitFor();
        await page.getByRole("link", { name: sponsored.name, exact: true }).waitFor();
        assert.deepEqual(
          queries.filter((query) => query.body.surface === "article").at(-1).body
            .exclude_product_ids,
          [],
          `${platform}: ${failure} history cannot suppress delivery`,
        );
        assert.equal(await page.locator(".preview-sidebar .partner-placement-label").count(), 2);
        assert.equal(await page.locator(".partner-placement button").count(), 0);
      }
      await page.evaluate(() => sessionStorage.removeItem("test:placement-storage"));
      await page.evaluate(() =>
        localStorage.removeItem("devfeed:partner-placement-preferences:v1"),
      );
      await page.goto(`${base}/articles/${news.slug}`);
      await page.getByRole("heading", { name: news.title, level: 1, exact: true }).waitFor();
      await page.waitForTimeout(300);
      assert.equal(
        await page.locator(".partner-placement").count(),
        0,
        `${platform}: no random fallback`,
      );
      const resetPreferences = async () => {
        await page.evaluate(() =>
          localStorage.removeItem("devfeed:partner-placement-preferences:v1"),
        );
      };
      const assertGrid = async (label) => {
        await page.locator(".article-grid > .partner-placement").first().waitFor();
        const children = await page
          .locator(".article-grid > *")
          .evaluateAll((nodes) =>
            nodes.map((node) =>
              node.classList.contains("partner-placement") ? "product" : "article",
            ),
          );
        assert.equal(
          children.filter((kind) => kind === "product").length,
          2,
          `${platform}: ${label} maximum two cards`,
        );
        assert.equal(
          children[4],
          "product",
          `${platform}: ${label} first product follows four articles`,
        );
        assert.equal(
          children[13],
          "product",
          `${platform}: ${label} second product follows twelve articles`,
        );
        assert.equal(
          await page.locator(".partner-placements").count(),
          0,
          `${platform}: ${label} has no separate recommendation banner`,
        );
        assert.equal(await page.getByText("Relevant product", { exact: true }).count(), 0);
        assert.equal(await page.getByText(ordinary.reason, { exact: true }).count(), 0);
        assert.equal(
          await page.getByRole("button", { name: `Hide ${sponsored.name}`, exact: true }).count(),
          0,
          `${platform}: ${label} sponsored card has no dismissal control`,
        );
        assert.equal(
          await page.getByRole("button", { name: `Hide ${ordinary.name}`, exact: true }).count(),
          0,
          `${platform}: ${label} non-campaign card has no dismissal control`,
        );
        assert.deepEqual(
          await page.locator(".article-grid .partner-placement-label").allTextContents(),
          ["Sponsored", "Sponsored"],
        );
        assert.equal(await page.locator(".partner-placement button").count(), 0);
        await assertSponsoredPill(
          page,
          ".article-grid .partner-placement-label",
          `${platform}: ${label}`,
          ".article-grid > .article-card .card-meta-type",
        );
        assert.deepEqual(
          await page
            .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
            .allTextContents(),
          feedArticles.map((item) => item.title),
          `${platform}: ${label} article order retained`,
        );
      };
      await page.goto(`${base}/topics/typescript`);
      await assertGrid("topic");
      assert.ok(
        queries.some((query) => query.body.surface === "topic" && query.body.topic_id === topic.id),
      );
      assert.equal(
        queries.filter((query) => query.body.surface === "topic").at(-1).body
          .allow_sponsored_fallback,
        undefined,
      );
      await resetPreferences();
      await page.goto(`${base}/latest`);
      await assertGrid("preview background");
      const feedRequestsBeforePreview = queries.filter(
        (query) => query.body.surface === "feed",
      ).length;
      await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
      await page.waitForFunction(
        (id) =>
          JSON.parse(
            localStorage.getItem("devfeed:partner-placement-preferences:v1"),
          )?.entries.some((entry) => entry.id === id && entry.count > 0),
        ordinary.product_id,
      );
      const previewedArticle = page
        .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
        .nth(8);
      await previewedArticle.scrollIntoViewIfNeeded();
      await previewedArticle.focus();
      const beforePreview = await previewedArticle.boundingBox();
      await previewedArticle.evaluate((element) => {
        element.dataset.previewPreserved = "true";
      });
      await resetPreferences();
      await previewedArticle.click();
      await page.locator(".article-modal .preview-sidebar .partner-placement").first().waitFor();
      await assertSponsoredPill(
        page,
        ".article-modal .preview-sidebar .partner-placement-label",
        `${platform}: modal preview`,
        ".article-grid > .article-card .card-meta-type",
      );
      assert.equal(
        await page.locator(".article-grid > .partner-placement:visible").count(),
        0,
        `${platform}: article preview owns visible product inventory`,
      );
      assert.equal(
        await page.locator('.article-grid > .partner-placement[data-suspended="true"]').count(),
        2,
        `${platform}: background cards keep their DOM while hidden`,
      );
      assert.ok(
        await page
          .locator(".article-grid > .partner-placement")
          .evaluateAll((cards) =>
            cards.every(
              (card) =>
                card.getBoundingClientRect().height > 0 &&
                card.getAttribute("aria-hidden") === "true" &&
                card.hasAttribute("inert"),
            ),
          ),
        `${platform}: suspended background cards keep geometry and leave the interaction/accessibility tree`,
      );
      await page
        .locator(".article-modal .preview-sidebar .partner-placement")
        .first()
        .scrollIntoViewIfNeeded();
      await page.waitForFunction(
        (id) =>
          JSON.parse(
            localStorage.getItem("devfeed:partner-placement-preferences:v1"),
          )?.entries.some((entry) => entry.id === id && entry.count > 0),
        ordinary.product_id,
      );
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await page.locator(".article-modal").waitFor({ state: "hidden" });
      await assertGrid("returned feed");
      const afterPreview = await previewedArticle.boundingBox();
      assert.ok(
        Math.abs(afterPreview.y - beforePreview.y) <= 2,
        `${platform}: preview return preserves scrolled article position (${beforePreview.y} -> ${afterPreview.y})`,
      );
      assert.equal(
        await previewedArticle.evaluate((element) => document.activeElement === element),
        true,
        `${platform}: preview returns focus to the same reading article`,
      );
      assert.equal(
        queries.filter((query) => query.body.surface === "feed").length,
        feedRequestsBeforePreview,
        `${platform}: closing preview does not reload placements`,
      );
      assert.equal(
        await page
          .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
          .nth(8)
          .getAttribute("data-preview-preserved"),
        "true",
        `${platform}: preview preserves background article DOM`,
      );
      const impressionsBeforeReturn = events.filter((event) => event.kind === "impression").length;
      const countsBeforeReturn = await page.evaluate(() =>
        JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1"))?.entries.map(
          ({ id, count }) => ({ id, count }),
        ),
      );
      await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(1200);
      assert.equal(
        events.filter((event) => event.kind === "impression").length,
        impressionsBeforeReturn,
        `${platform}: returning to the same card does not report another impression`,
      );
      assert.deepEqual(
        await page.evaluate(() =>
          JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1"))?.entries.map(
            ({ id, count }) => ({ id, count }),
          ),
        ),
        countsBeforeReturn,
        `${platform}: returning to the same card does not consume another local impression`,
      );
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
      await waitForVisibleProductLogos(page);
      await page.screenshot({ path: `reports/partner-placements/${platform}-topic-mobile.png` });
      assert.ok(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        `${platform}: mobile has no horizontal overflow`,
      );
      await resetPreferences();
      await page.goto(`${base}/articles/${article.slug}`);
      await page.locator(".preview-sidebar .partner-placement").first().waitFor();
      await assertSponsoredPill(
        page,
        ".preview-sidebar .partner-placement-label",
        `${platform}: mobile preview`,
      );
      assert.equal(
        await page.locator(".preview-copy .partner-placement").count(),
        0,
        `${platform}: mobile products remain outside article reading body`,
      );
      await page.locator(".preview-sidebar .partner-placement").first().scrollIntoViewIfNeeded();
      await waitForVisibleProductLogos(page);
      await page.screenshot({ path: `reports/partner-placements/${platform}-article-mobile.png` });
      await page
        .locator(
          `.preview-sidebar .partner-placement[data-product-id="${sponsored.product_id}"] .partner-placement-label`,
        )
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `reports/partner-placements/${platform}-sponsored-pill-preview-mobile.png`,
      });
      assert.ok(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        `${platform}: mobile preview has no overflow`,
      );
      await page.setViewportSize({ width: 1280, height: 900 });
      for (const { route, width } of [
        { route: "/", width: 1280 },
        { route: "/latest?q=typescript", width: 1280 },
        { route: "/tutorials", width: 1280 },
        { route: "/latest?q=typescript", width: 390 },
      ]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        await resetPreferences();
        queries = [];
        let release;
        deliveryGate = new Promise((resolve) => {
          release = resolve;
        });
        await page.goto(`${base}${route}`);
        const readingArticle = page
          .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
          .nth(8);
        await readingArticle.waitFor();
        await readingArticle.scrollIntoViewIfNeeded();
        await readingArticle.focus();
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const before = await readingArticle.boundingBox();
        assert.ok(
          await page.evaluate(() => scrollY > 0),
          `${platform}: reader is scrolled past the first insertion point`,
        );
        deliveryGate = null;
        release();
        await assertGrid(`anonymous ${route}`);
        assert.equal(
          await readingArticle.evaluate((element) => element === document.activeElement),
          true,
          `${platform}: optional delivery preserves article keyboard focus`,
        );
        const after = await readingArticle.boundingBox();
        assert.ok(
          Math.abs(after.y - before.y) <= 2,
          `${platform}: optional delivery keeps the scrolled reading article position (${before.y} -> ${after.y})`,
        );
        assert.ok(
          await page.evaluate(() => scrollY > 0),
          `${platform}: optional delivery does not reset reading scroll`,
        );
        const feedQuery = queries.find((query) => query.body.surface === "feed");
        assert.equal(
          feedQuery?.pathname,
          "/v1/partner-placements/query",
          `${platform}: public results use anonymous delivery`,
        );
        assert.deepEqual(
          feedQuery.body.article_ids,
          feedArticles.map((item) => item.id),
          `${platform}: delivery grounded in visible article results`,
        );
        assert.equal(feedQuery.body.article_ids.length, 24);
        assert.equal(
          feedQuery.body.allow_sponsored_fallback,
          route === "/",
          `${platform}: only the unfiltered main feed permits Sponsored fallback`,
        );
        if (route === "/") {
          await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
          await page.mouse.move(20, 20);
          await waitForVisibleProductLogos(page);
          await page.screenshot({
            path: `reports/partner-placements/${platform}-feed-desktop.png`,
          });
        }
        if (width === 390) {
          await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
          await waitForVisibleProductLogos(page);
          await page.screenshot({
            path: `reports/partner-placements/${platform}-search-mobile.png`,
          });
        }
      }
      await page.setViewportSize({ width: 1280, height: 900 });
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        await resetPreferences();
        queries = [];
        let release;
        deliveryGate = new Promise((resolve) => {
          release = resolve;
        });
        // Reopen the packaged reader for the second viewport too; navigating to
        // an unchanged extension hash deliberately retains the current delivery.
        await page.goto("about:blank");
        await page.goto(`${base}/search?q=typescript`);
        const readingResult = page.locator(".search-section-articles .search-result-link").nth(8);
        await readingResult.waitFor();
        await readingResult.scrollIntoViewIfNeeded();
        await readingResult.focus();
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const before = await readingResult.boundingBox();
        deliveryGate = null;
        release();
        await page
          .locator(".search-section-articles .search-result-list > .partner-placement")
          .first()
          .waitFor();
        const rows = await page
          .locator(".search-section-articles .search-result-list > *")
          .evaluateAll((elements) =>
            elements.map((element) =>
              element.classList.contains("partner-placement") ? "product" : "article",
            ),
          );
        assert.equal(
          rows.filter((row) => row === "product").length,
          2,
          `${platform}: actual search maximum two products`,
        );
        assert.equal(rows[4], "product");
        assert.equal(rows[13], "product");
        assert.equal(await page.locator(".search-related-results .partner-placement").count(), 0);
        assert.equal(await page.locator(".partner-placements").count(), 0);
        assert.equal(await page.getByText(ordinary.reason, { exact: true }).count(), 0);
        assert.equal(await page.getByText("Relevant product", { exact: true }).count(), 0);
        assert.equal(
          await readingResult.evaluate((element) => element === document.activeElement),
          true,
        );
        const after = await readingResult.boundingBox();
        assert.ok(
          Math.abs(after.y - before.y) <= 2,
          `${platform}: actual search keeps scrolled result position (${before.y} -> ${after.y})`,
        );
        const searchQuery = queries.find((query) => query.body.surface === "feed");
        assert.equal(searchQuery.pathname, "/v1/partner-placements/query");
        assert.equal(
          searchQuery.body.allow_sponsored_fallback,
          false,
          `${platform}: actual search stays contextual`,
        );
        assert.deepEqual(
          searchQuery.body.article_ids,
          feedArticles.map((item) => item.id),
        );
        await page
          .locator(".search-section-articles .search-result-list > .partner-placement")
          .first()
          .scrollIntoViewIfNeeded();
        await page.mouse.move(5, 5);
        await waitForVisibleProductLogos(page);
        await page.screenshot({
          path: `reports/partner-placements/${platform}-actual-search-${width === 390 ? "mobile" : "desktop"}.png`,
        });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      }
      for (const section of ["topics", "sources", "tags"]) {
        queries = [];
        await page.goto(`${base}/search?q=typescript&section=${section}`);
        await page.locator(`.search-section-${section} .search-result-link`).first().waitFor();
        await page.waitForTimeout(200);
        assert.equal(
          await page.locator(".partner-placement").count(),
          0,
          `${platform}: no unrelated article product inside ${section} search`,
        );
        assert.equal(
          queries.length,
          0,
          `${platform}: ${section} search reserves no product inventory`,
        );
      }
      for (const theme of ["light", "dark"]) {
        for (const width of [1280, 390]) {
          for (const mode of ["standard", "no-og"]) {
            mediaMode = mode;
            await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
            await resetPreferences();
            await page.evaluate((value) => localStorage.setItem("devfeed:theme", value), theme);
            await page.goto("about:blank");
            await page.goto(`${base}/topics/typescript`);
            await assertGrid(`article-shaped ${theme} ${width} ${mode}`);
            const card = page.locator(
              `.article-grid > .partner-placement[data-product-id="${ordinary.product_id}"]`,
            );
            await card.scrollIntoViewIfNeeded();
            await page.mouse.move(5, 5);
            await waitForVisibleProductLogos(page);
            const geometry = await card.evaluate((product) => {
              const neighbor = product.previousElementSibling;
              const box = (node) => {
                const rect = node.getBoundingClientRect();
                return { width: rect.width, height: rect.height };
              };
              const hero = product.querySelector(".card-image");
              const articleHero = neighbor.querySelector(".card-image");
              const image = hero.querySelector("img");
              return {
                product: box(product),
                article: box(neighbor),
                hero: box(hero),
                articleHero: box(articleHero),
                radius: getComputedStyle(product).borderRadius,
                articleRadius: getComputedStyle(neighbor).borderRadius,
                titleSize: getComputedStyle(product.querySelector("h2")).fontSize,
                articleTitleSize: getComputedStyle(neighbor.querySelector("h2")).fontSize,
                image: image && { src: image.currentSrc, fit: getComputedStyle(image).objectFit },
                dark: document.documentElement.classList.contains("dark"),
              };
            });
            assert.equal(geometry.dark, theme === "dark");
            assert.ok(Math.abs(geometry.product.width - geometry.article.width) <= 1);
            assert.ok(
              Math.abs(geometry.product.height - geometry.article.height) <= 2,
              `${platform}: ${theme} ${width} ${mode} product/article height ${geometry.product.height}/${geometry.article.height}`,
            );
            assert.ok(Math.abs(geometry.hero.height - geometry.articleHero.height) <= 1);
            assert.ok(Math.abs(geometry.hero.width / geometry.hero.height - 16 / 9) < 0.02);
            assert.equal(geometry.radius, geometry.articleRadius);
            assert.equal(geometry.titleSize, geometry.articleTitleSize);
            assert.equal(geometry.image.fit, mode === "standard" ? "cover" : "contain");
            assert.ok(
              geometry.image.src.includes(
                mode === "standard" ? "/og/validator-" : "/validator.svg",
              ),
            );
            assert.equal(await card.locator("time, .engagement-button").count(), 0);
            assert.ok(
              await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
            );
            await page.screenshot({
              path: `reports/partner-placements/${platform}-article-card-${theme}-${width === 390 ? "mobile" : "desktop"}-${mode}.png`,
            });
            if (mode === "standard") {
              await page
                .locator(
                  `.article-grid .partner-placement[data-product-id="${sponsored.product_id}"] .partner-placement-label`,
                )
                .scrollIntoViewIfNeeded();
              await waitForVisibleProductLogos(page);
              await page.screenshot({
                path: `reports/partner-placements/${platform}-sponsored-pill-${theme}-${width === 390 ? "mobile" : "desktop"}.png`,
              });
            }
          }
        }
      }
      for (const mode of ["broken-og", "no-assets"]) {
        mediaMode = mode;
        await resetPreferences();
        await page.goto("about:blank");
        await page.goto(`${base}/topics/typescript`);
        await assertGrid(mode);
        const card = page.locator(
          `.article-grid > .partner-placement[data-product-id="${ordinary.product_id}"]`,
        );
        await card.scrollIntoViewIfNeeded();
        await waitForVisibleProductLogos(page);
        assert.equal(await card.locator(".partner-placement-hero-logo").count(), 1);
        if (mode === "broken-og") {
          assert.equal(await card.locator(".partner-placement-logo img").count(), 1);
        } else {
          assert.equal(await card.locator(".partner-placement-logo img").count(), 0);
          assert.equal(await card.locator(".partner-placement-logo").textContent(), "TV");
        }
      }
      mediaMode = "standard";
      await page.setViewportSize({ width: 1280, height: 900 });
      await resetPreferences();
      signedIn = true;
      await page.goto(`${base}/`);
      await page.reload();
      await assertGrid("personalized feed");
      assert.ok(
        queries.some(
          (query) =>
            query.pathname === "/v1/user/partner-placements/query" &&
            query.body.surface === "feed" &&
            query.body.allow_sponsored_fallback === true,
        ),
        `${platform}: personalized endpoint`,
      );
      await page.evaluate(() =>
        localStorage.removeItem("devfeed:partner-placement-preferences:v1"),
      );
      queries = [];
      await page.goto(`${base}/articles/${article.slug}`);
      await page
        .locator(".article-modal")
        .getByRole("link", { name: ordinary.name, exact: true })
        .waitFor();
      await page.waitForTimeout(300);
      assert.equal(
        await page.locator(".article-modal .partner-placement").count(),
        2,
        `${platform}: signed-in direct article owns visible placements`,
      );
      assert.equal(
        await page.locator(".partner-placement").count(),
        2,
        `${platform}: background feed has no competing placements`,
      );
      assert.equal(
        queries.filter((query) => query.pathname === "/v1/user/partner-placements/query").length,
        0,
        `${platform}: background feed reserves no paid inventory`,
      );
      console.log(
        `${platform}: built web/extension in-grid homepage, search, category and topic cards; every product disclosed with distinct amber Sponsored pills and no dismissal; branded preview sidebar; focus/layout stability; signed campaign and non-campaign tracking; legacy hides/limits and failed optional storage cannot suppress delivery; article priority passed`,
      );
    } finally {
      await context.close();
    }
  }
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
  await rm(temp, { recursive: true, force: true });
}
