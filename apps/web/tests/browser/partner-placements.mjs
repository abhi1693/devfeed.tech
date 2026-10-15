import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { createServer, request as httpRequest } from "node:http";
import { cp, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { chromium } from "playwright";

// Exercise actual production reader bundles and packaged extensions, with controlled
// delivery responses. Backend integration tests prove relevance and signed receipts.
const receiptIssuedAt = Math.floor(Date.now() / 1000);
const receiptSigningKey = createHmac("sha256", "disposable-placement-receipt-signing-key")
  .update("devfeed:partner-delivery:v1")
  .digest();
function placementReceipt(assetId, deliveryId) {
  const payload = `v1.${assetId.replaceAll("-", "")}.${deliveryId.replaceAll("-", "")}.${receiptIssuedAt}`;
  return `${payload}.${createHmac("sha256", receiptSigningKey).update(payload).digest("hex")}`;
}

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
  receipt: placementReceipt(
    "22222222-2222-4222-8222-222222222222",
    "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  ),
};
const sponsored = {
  ...ordinary,
  product_id: "33333333-3333-4333-8333-333333333333",
  asset_id: "44444444-4444-4444-8444-444444444444",
  campaign_id: "55555555-5555-4555-8555-555555555555",
  name: "Sponsored validator",
  partner_name: "Paid partner",
  sponsored: true,
  receipt: placementReceipt(
    "44444444-4444-4444-8444-444444444444",
    "ffffffff-ffff-4fff-8fff-ffffffffffff",
  ),
  logo_url: "https://assets.partner.example/broken.png",
  image_url: "https://assets.partner.example/og/broken.png",
  image_variants: [],
};
const customProductImage = {
  image_url: "https://assets.partner.example/ads/validator-original.svg?v=upload-2",
  image_variants: [
    { url: "https://assets.partner.example/ads/validator-400.svg?v=upload-2", width: 400 },
    { url: "https://assets.partner.example/ads/validator-800.svg?v=upload-2", width: 800 },
  ],
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
// Pin the built packages while repository hooks may rebuild their output directories.
await cp(path.resolve("apps/extensions/dist"), path.join(temp, "extensions"), { recursive: true });
const reportDirectory = process.env.CI
  ? "reports/partner-placements"
  : await mkdtemp(path.join(tmpdir(), "devfeed-placement-reports-"));
await mkdir(reportDirectory, { recursive: true });
console.log(`Placement browser artifacts: ${reportDirectory}`);
let signedIn = false;
let queries = [];
let events = [];
let deliveryGate = null;
let mediaMode = "standard";
let experienceLevel = null;
let profileWrites = [];
let nativeMode = null;
let organicCount = 24;
let promotedArrival = false;
let organicPagesDelivered = 0;
let placementLimited = false;
const callerFixtureKey = "disposable-dedicated-caller-proof-key";
const callerFixtureIps = { web: "203.0.113.20", chrome: "203.0.113.30", edge: "203.0.113.40" };
let callerFixtureIp = callerFixtureIps.web;
const observedCallerProofs = new Map();
const observedCallerIps = new Set();

function verifyCallerProof(headers, pathname) {
  const proof = headers["x-devfeed-partner-caller"];
  assert.equal(typeof proof, "string", "The production gateway signs caller identity");
  const parts = /^v1\.(\d+)\.(.+)\.([a-f0-9]{64})$/.exec(proof);
  assert.ok(parts, "Caller proof uses the versioned, bounded protocol");
  const [, timestamp, ip, signature] = parts;
  assert.ok(Object.values(callerFixtureIps).includes(ip), "Only trusted edge addresses are signed");
  assert.ok(Math.abs(Date.now() / 1000 - Number(timestamp)) < 60, "Caller proof is fresh");
  const signingKey = createHmac("sha256", callerFixtureKey)
    .update("devfeed:partner-caller:v1")
    .digest();
  const expected = createHmac("sha256", signingKey)
    .update(`v1.${timestamp}.${ip}\nPOST\n${pathname}`)
    .digest("hex");
  assert.equal(signature, expected, "The proof binds the caller to this method and upstream path");
  assert.equal(headers["cf-connecting-ip"], undefined, "Raw edge headers are not forwarded");
  observedCallerProofs.set(pathname, new Set([...(observedCallerProofs.get(pathname) ?? []), ip]));
  observedCallerIps.add(ip);
}

function impressionCount(receipt) {
  return events.filter((event) => event.kind === "impression" && event.receipt === receipt).length;
}

async function waitForImpression(page, receipt, previous = 0) {
  for (let attempt = 0; attempt < 200 && impressionCount(receipt) <= previous; attempt++)
    await page.waitForTimeout(50);
  assert.ok(
    impressionCount(receipt) > previous,
    "A viewable placement reports its signed impression",
  );
}

async function assertNoPlacementHistory(page, label) {
  assert.deepEqual(
    await page.evaluate(() => window.__placementHistoryAccess),
    { reads: 0, writes: 0, removals: 0 },
    `${label}: placement delivery and tracking never access abandoned local history`,
  );
}

async function assertMountedPlacementExpiry(context, base, platform) {
  // Playwright's clock applies to the entire context. Finish the ordinary UI
  // scenarios before replacing its pages and installing controlled timers.
  const previousPages = context.pages();
  // Keep a blank tab open while replacing reader pages in a persistent context.
  await context.newPage();
  await Promise.all(previousPages.map((page) => page.close()));
  await context.clock.install({ time: new Date(receiptIssuedAt * 1000) });
  for (const mode of ["foreground", "background-wake"]) {
    await context.clock.setSystemTime(receiptIssuedAt * 1000);
    const page = await context.newPage();
    await page.addInitScript(() => {
      sessionStorage.setItem("devfeed:dev-card-promo-dismissed", "true");
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    });
    try {
      await page.goto(`${base}/latest?q=typescript`);
      const cards = page.locator(".article-grid > .partner-placement");
      await cards.first().waitFor();
      await cards.nth(1).waitFor();
      const reading = page
        .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
        .nth(8);
      await reading.scrollIntoViewIfNeeded();
      await reading.focus();
      await reading.evaluate((element) =>
        element.setAttribute("data-expiry-reading-anchor", "true"),
      );
      await page.clock.fastForward(59 * 60_000);
      await page.waitForTimeout(100);
      assert.equal(
        await page.locator(".article-grid > .partner-placement:visible").count(),
        2,
        `${platform}/${mode}: mounted cards remain available before receipt expiry`,
      );
      const beforeReading = await reading.boundingBox();
      const beforeCards = await cards.evaluateAll((elements) =>
        elements.map((element, index) => {
          element.setAttribute("data-expiry-fixture-cell", String(index));
          const { width, height } = element.getBoundingClientRect();
          return { width, height };
        }),
      );
      const beforeRequests = queries.length;
      const beforeEvents = events.length;
      if (mode === "foreground") await page.clock.fastForward(61_000);
      else {
        await page.evaluate(() =>
          Object.defineProperty(document, "visibilityState", {
            configurable: true,
            value: "hidden",
          }),
        );
        // Move wall time beyond the deadline without running the expiry timer,
        // then wake the hidden tab through the browser visibility boundary.
        await page.clock.setSystemTime((receiptIssuedAt + 3601) * 1000);
        await page.evaluate(() => {
          Object.defineProperty(document, "visibilityState", {
            configurable: true,
            value: "visible",
          });
          document.dispatchEvent(new Event("visibilitychange"));
        });
      }
      await page.waitForFunction(() => {
        const cards = [...document.querySelectorAll(".article-grid > .partner-placement")];
        return (
          cards.length === 2 &&
          cards.every(
            (card) =>
              card.getAttribute("aria-hidden") === "true" &&
              card.hasAttribute("inert") &&
              getComputedStyle(card).visibility === "hidden",
          )
        );
      });
      assert.equal(await page.getByRole("link", { name: ordinary.name, exact: true }).count(), 0);
      assert.equal(await page.getByRole("link", { name: sponsored.name, exact: true }).count(), 0);
      const afterCards = await cards.evaluateAll((elements) =>
        elements.map((element, index) => {
          const { width, height } = element.getBoundingClientRect();
          return {
            width,
            height,
            same: element.getAttribute("data-expiry-fixture-cell") === String(index),
          };
        }),
      );
      for (const [index, cell] of afterCards.entries()) {
        assert.ok(
          cell.same,
          `${platform}/${mode}: receipt expiry retains the same reserved card node`,
        );
        assert.ok(cell.width > 0 && cell.height > 0);
        assert.ok(Math.abs(cell.width - beforeCards[index].width) <= 1);
        assert.ok(Math.abs(cell.height - beforeCards[index].height) <= 1);
      }
      const afterReading = await reading.boundingBox();
      assert.ok(
        Math.abs(afterReading.y - beforeReading.y) <= 2,
        `${platform}/${mode}: expiry preserves the scrolled reading article position`,
      );
      assert.equal(
        await reading.evaluate(
          (element) =>
            element.getAttribute("data-expiry-reading-anchor") === "true" &&
            document.activeElement === element,
        ),
        true,
        `${platform}/${mode}: expiry preserves reading DOM and keyboard focus`,
      );
      await cards.locator(".partner-placement-link").evaluateAll((links) => {
        for (const link of links) {
          link.addEventListener("click", (event) => event.preventDefault(), { once: true });
          link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        }
      });
      await page.clock.runFor(2000);
      await page.waitForTimeout(100);
      assert.equal(events.length, beforeEvents, `${platform}/${mode}: expired cards stop tracking`);
      assert.equal(
        queries.length,
        beforeRequests,
        `${platform}/${mode}: expiry does not refresh or rotate the reading context`,
      );
      await assertNoPlacementHistory(page, `${platform}/${mode}`);
    } finally {
      await page.close();
    }
  }
  await assertOrganicRestoration(context, base, platform);
}

async function assertOrganicRestoration(context, base, platform) {
  for (const surface of ["grid", "search"]) {
    await context.clock.setSystemTime(receiptIssuedAt * 1000);
    nativeMode = "sponsored-content";
    promotedArrival = true;
    const delivered = organicPagesDelivered;
    const page = await context.newPage();
    await page.addInitScript(() => {
      sessionStorage.setItem("devfeed:dev-card-promo-dismissed", "true");
    });
    try {
      await page.goto(
        `${base}/${surface === "search" ? "search?q=typescript&section=articles" : "latest?q=typescript"}`,
      );
      const promoted = page.locator('[data-ad-format="sponsored_content"]');
      await promoted.waitFor();
      await page.locator('.pagination[data-has-more="true"]').first().scrollIntoViewIfNeeded();
      // Completed search pagination is intentionally hidden when its end message is empty.
      await page
        .locator('.pagination[data-has-more="false"]')
        .first()
        .waitFor({ state: "attached" });
      assert.equal(
        organicPagesDelivered,
        delivered + 1,
        `${platform}/${surface}: a later page really returns the promoted article organically`,
      );
      assert.equal(
        await page.getByRole("link", { name: promotedArticle.title, exact: true }).count(),
        1,
      );
      const selector =
        surface === "grid"
          ? ".article-grid > .article-card:not(.partner-placement) .card-open-link"
          : ".search-section-articles .search-result-link";
      // Wait for the article list itself to settle after pagination before
      // measuring layout; the pagination marker alone does not assert its contents.
      await page.waitForFunction(
        (selector) => document.querySelectorAll(selector).length === 24,
        selector,
      );
      assert.deepEqual(
        await page.locator(selector).allTextContents(),
        feedArticles.map((item) => item.title),
        `${platform}/${surface}: pagination retains the original articles exactly once`,
      );
      const reading = page.locator(selector).nth(8);
      await reading.scrollIntoViewIfNeeded();
      await reading.focus();
      await reading.evaluate((node) =>
        node.setAttribute("data-organic-restoration-anchor", "true"),
      );
      const before = await reading.boundingBox();
      const requests = queries.length;
      await page.clock.fastForward(60 * 60_000 + 1000);
      await page.waitForFunction(
        () =>
          document
            .querySelector('[data-ad-format="sponsored_content"]')
            ?.getAttribute("data-suspended") === "true",
      );
      const organic = page.getByRole("link", { name: promotedArticle.title, exact: true });
      await organic.waitFor();
      assert.equal(await organic.count(), 1);
      assert.equal(
        await organic.evaluate((node) =>
          node.closest("article").classList.contains("partner-placement"),
        ),
        false,
      );
      await page.waitForFunction(
        (selector) => document.querySelectorAll(selector).length === 25,
        selector,
      );
      assert.deepEqual(
        await page.locator(selector).allTextContents(),
        [...feedArticles, promotedArticle].map((item) => item.title),
        `${platform}/${surface}: expiry restores the organic article exactly once in page order`,
      );
      const after = await reading.boundingBox();
      assert.ok(
        Math.abs(after.y - before.y) <= 2,
        `${platform}/${surface}: organic restoration preserves viewport reading position`,
      );
      assert.equal(
        await reading.evaluate(
          (node) =>
            node === document.activeElement && node.hasAttribute("data-organic-restoration-anchor"),
        ),
        true,
      );
      assert.equal(await promoted.getAttribute("aria-hidden"), "true");
      assert.equal(await promoted.evaluate((node) => node.hasAttribute("inert")), true);
      assert.equal(
        queries.length,
        requests,
        `${platform}/${surface}: organic restoration does not request new placements`,
      );
      await assertNoPlacementHistory(page, `${platform}/${surface}: organic restoration`);
      await page.screenshot({
        path: `${reportDirectory}/${platform}-${surface}-organic-restoration.png`,
      });
    } finally {
      await page.close();
      promotedArrival = false;
      nativeMode = null;
    }
  }
}
const promotedArticle = {
  ...article,
  id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  slug: "approved-sponsored-typescript-guide",
  title: "A sponsored guide to TypeScript boundaries",
  author: "Engineering team",
  image_url: "https://assets.partner.example/articles/typescript-guide.svg",
  summary: "A practical tutorial about validating TypeScript application inputs. ".repeat(500),
  sponsor_names: [sponsored.partner_name],
};
const homepageSponsor = { ...ordinary, ad_format: "homepage_sponsorship" };
const sponsoredContent = {
  ...sponsored,
  ...customProductImage,
  ad_format: "sponsored_content",
  article: promotedArticle,
  url: `/articles/${promotedArticle.slug}`,
};
const engagementPlacement = { ...ordinary, ad_format: "engagement" };

function organicPage(url) {
  const nextCursor = url.pathname.endsWith("/search") ? "2" : "promoted";
  const appended =
    promotedArrival &&
    (url.searchParams.get("cursor") === "promoted" || url.searchParams.get("page") === "2");
  const items = appended ? [promotedArticle] : feedArticles.slice(0, organicCount);
  if (appended) organicPagesDelivered += 1;
  return {
    items:
      nativeMode === "disclosure"
        ? items.map((item, i) =>
            i === 0 ? { ...item, sponsor_names: [sponsored.partner_name] } : item,
          )
        : items,
    next_cursor: promotedArrival && !appended ? nextCursor : null,
  };
}

function productMedia(item) {
  if (item.product_id !== ordinary.product_id || mediaMode === "standard") return item;
  if (mediaMode === "custom") return { ...item, ...customProductImage };
  if (mediaMode.startsWith("broken-custom"))
    return {
      ...item,
      image_url: "https://assets.partner.example/ads/broken.png",
      image_variants: [],
      logo_url: mediaMode === "broken-custom-logo" ? sponsored.logo_url : ordinary.logo_url,
    };
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

async function assertSponsoredCardInteraction(page, card, label, screenshot) {
  const read = () =>
    card.evaluate((element) => {
      const style = getComputedStyle(element);
      const pill = getComputedStyle(element.querySelector(".partner-placement-label"));
      const link = element.querySelector(".partner-placement-link");
      const rect = element.getBoundingClientRect();
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const context = canvas.getContext("2d");
      const rgba = (color) => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        return Array.from(context.getImageData(0, 0, 1, 1).data);
      };
      return {
        border: rgba(style.borderTopColor),
        neutral: rgba(style.getPropertyValue("--border")),
        amber: rgba(pill.borderTopColor),
        shadow: style.boxShadow,
        transform: style.transform,
        transitions: style.transitionDuration.split(",").map(parseFloat),
        focusVisible: link.matches(":focus-visible"),
        outline: getComputedStyle(link).outlineWidth,
        width: rect.width,
        height: rect.height,
      };
    });
  const settle = () => page.waitForTimeout(250);
  const leave = async () => {
    await page.mouse.move(5, 5);
    await card.evaluate(() => document.activeElement?.blur());
    await settle();
  };
  await leave();
  const resting = await read();
  assert.deepEqual(resting.border, resting.neutral, `${label}: neutral at rest`);
  await card.hover();
  await settle();
  const hovered = await read();
  assert.deepEqual(hovered.border, hovered.amber, `${label}: hover matches Sponsored pill`);
  assert.notEqual(hovered.shadow, "none", `${label}: subtle hover depth`);
  assert.equal(hovered.width, resting.width, `${label}: hover preserves width`);
  assert.equal(hovered.height, resting.height, `${label}: hover preserves height`);
  const clip = await card.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const x = Math.max(0, Math.floor(rect.x - 12));
    const y = Math.max(0, Math.floor(rect.y - 12));
    return {
      x,
      y,
      width: Math.min(innerWidth, Math.ceil(rect.right + 12)) - x,
      height: Math.min(innerHeight, Math.ceil(rect.bottom + 12)) - y,
    };
  });
  await page.screenshot({ path: screenshot, clip });
  await leave();
  assert.deepEqual((await read()).border, resting.border, `${label}: hover clears on leave`);
  await page.keyboard.press("Tab");
  await card.locator(".partner-placement-link").focus();
  await settle();
  const focused = await read();
  assert.ok(focused.focusVisible, `${label}: keyboard focus is visible`);
  assert.deepEqual(focused.border, focused.amber, `${label}: keyboard gets same amber border`);
  assert.ok(parseFloat(focused.outline) >= 2, `${label}: link retains visible focus outline`);
  await leave();
  await page.emulateMedia({ reducedMotion: "reduce" });
  try {
    await card.hover();
    const reduced = await read();
    assert.deepEqual(reduced.border, reduced.amber, `${label}: reduced motion retains amber`);
    assert.ok(reduced.transitions.every((duration) => duration === 0));
    assert.equal(reduced.transform, "none", `${label}: reduced motion prevents lift`);
  } finally {
    await leave();
    await page.emulateMedia({ reducedMotion: "no-preference" });
  }
}

function response(requestPath, body, headers = {}, extension = false) {
  const url = new URL(requestPath, "https://devfeed.tech");
  const pathname = url.pathname.replace(/^\/api/, "");
  if (
    !extension &&
    ["/v1/partner-placements/query", "/v1/partner-tracking/events"].includes(pathname)
  )
    verifyCallerProof(headers, pathname);
  if (["/v1/partner-placements/query", "/v1/user/partner-placements/query"].includes(pathname)) {
    assert.equal(headers.authorization, undefined);
    if (pathname === "/v1/partner-placements/query") {
      assert.equal(headers.cookie, undefined);
      assert.equal(headers["x-csrf-token"], undefined);
    } else assert.equal(headers["x-csrf-token"], "fixture-csrf");
    assert.ok(
      Object.keys(body).every((key) =>
        [
          "surface",
          "article_id",
          "article_ids",
          "topic_id",
          "allow_sponsored_fallback",
          "exclude_product_ids",
          "ad_formats",
        ].includes(key),
      ),
      "Selection sends context only, never reader identity or audience attributes",
    );
    queries.push({ pathname, body });
    if (nativeMode) {
      assert.ok(Array.isArray(body.ad_formats), "Native formats are explicitly requested");
      if (!body.allow_sponsored_fallback)
        assert.ok(!body.ad_formats.includes("homepage_sponsorship"));
      if (body.surface !== "feed") assert.ok(!body.ad_formats.includes("sponsored_content"));
      const items =
        nativeMode === "disclosure"
          ? []
          : nativeMode === "sponsored-content"
            ? [sponsoredContent]
            : nativeMode === "home" && body.allow_sponsored_fallback
              ? [homepageSponsor, sponsoredContent]
              : [engagementPlacement, sponsored];
      return {
        items: items
          .filter(
            (item) =>
              body.ad_formats.includes(item.ad_format ?? "feed") &&
              !body.exclude_product_ids?.includes(item.product_id),
          )
          .map(productMedia),
      };
    }
    const eligible =
      body.surface === "topic" ||
      (pathname.includes("/user/") && body.surface === "feed") ||
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
  if (pathname === "/v1/user/settings/profile") {
    if (body) {
      assert.equal(headers["x-csrf-token"], "fixture-csrf");
      experienceLevel = body.experience_level;
      profileWrites.push(body);
    }
    return {
      display_name: "Reader",
      avatar_url: null,
      username: "fixture-reader",
      links: [],
      stack: [],
      experience_level: experienceLevel,
    };
  }
  if (pathname === "/v1/user/settings/feed")
    return { view: "cards", content_types: ["tutorial", "news"], languages: ["en"] };
  if (["/v1/user/preferences", "/v1/user/preferences/topics"].includes(pathname))
    return { topic_ids: [topic.id] };
  if (pathname === "/v1/user/preferences/sources") return { source_ids: [] };
  if (pathname === "/v1/user/engagement") return { items: [] };
  if (pathname === "/v1/user/feed")
    return {
      ...organicPage(url),
      status: "ready",
      has_interests: true,
      reasons: {},
      feed_kind: "personalized",
      refresh_state: "idle",
    };
  if (pathname === "/v1/feed") return organicPage(url);
  if (pathname === "/v1/search") {
    const page = organicPage(url);
    const sections = {
      articles: {
        items: page.items.map((item) => ({
          id: item.id,
          title: item.title,
          description: item.summary,
          href: `/articles/${item.slug}`,
          image_url: null,
          label: item.content_type,
          published_at: item.published_at,
          click_token: "fixture-result-token",
        })),
        next_cursor: page.next_cursor,
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
      sections: section
        ? Object.fromEntries(
            ["articles", "topics", "sources", "tags"]
              .filter((name) => name === section)
              .map((name) => [name, sections[name]]),
          )
        : sections,
    };
  }
  if (pathname === "/v1/feed/options")
    return { sources: [source], content_types: ["tutorial", "news"] };
  if (pathname === "/v1/topics/typescript") return topic;
  if (pathname === "/v1/topics") return extension ? { items: [topic], next_offset: null } : [topic];
  if (pathname === "/v1/sources")
    return extension ? { items: [source], next_offset: null } : [source];
  if (pathname.startsWith("/v1/articles/")) {
    const selected = pathname.endsWith(promotedArticle.slug)
      ? promotedArticle
      : nativeMode === "disclosure" && pathname.endsWith(article.slug)
        ? { ...article, sponsor_names: [sponsored.partner_name] }
        : pathname.endsWith(news.slug)
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
    const pathname = new URL(req.url, "http://fixture").pathname;
    if (placementLimited && pathname === "/v1/partner-placements/query") {
      res.writeHead(429, { "Content-Type": "application/json", "Retry-After": "47" });
      res.end(JSON.stringify({ detail: "private limiter infrastructure details" }));
      return;
    }
    const noContent = pathname === "/v1/partner-tracking/events";
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
// This fixture models a trusted edge that overwrites browser-supplied client-IP
// headers. All three readers then use the real production Next.js gateways.
const edge = createServer((req, res) => {
  const upstream = httpRequest(
    {
      hostname: "127.0.0.1",
      port,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, "cf-connecting-ip": callerFixtureIp },
    },
    (response) => {
      res.writeHead(response.statusCode, response.headers);
      response.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502);
    res.end();
  });
  req.pipe(upstream);
});
await new Promise((resolve) => edge.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${edge.address().port}`;
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
      DEVFEED_PARTNER_CALLER_KEY: callerFixtureKey,
      DEVFEED_PARTNER_CALLER_IP_HEADER: "cf-connecting-ip",
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
    callerFixtureIp = callerFixtureIps[platform];
    observedCallerProofs.clear();
    signedIn = false;
    queries = [];
    events = [];
    mediaMode = "standard";
    experienceLevel = null;
    profileWrites = [];
    nativeMode = null;
    organicCount = 24;
    promotedArrival = false;
    const extension = platform === "web" ? null : path.join(temp, "extensions", platform);
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
    const browserErrors = [];
    context.on("weberror", (error) => browserErrors.push(error.error().stack));
    context.on("requestfailed", (request) =>
      browserErrors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`),
    );
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
          const pathname = new URL(request.url()).pathname;
          if (
            ["/api/v1/partner-placements/query", "/api/v1/partner-tracking/events"].includes(
              pathname,
            )
          ) {
            assert.equal(request.headers().cookie, undefined);
            assert.equal(request.headers().authorization, undefined);
            assert.equal(request.headers()["x-devfeed-partner-caller"], undefined);
            const gateway = await fetch(new URL(pathname, origin), {
              method: "POST",
              headers: { "Content-Type": "application/json", "Accept-Encoding": "identity" },
              body: raw,
            });
            await route.fulfill({
              status: gateway.status,
              headers: Object.fromEntries(gateway.headers),
              body: Buffer.from(await gateway.arrayBuffer()),
            });
            return;
          }
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
              body: route.request().url().includes("/ads/")
                ? '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><rect width="1200" height="675" fill="#173b35"/><text x="100" y="280" fill="#b7f3c8" font-size="64" font-family="sans-serif">A custom product creative</text><text x="100" y="390" fill="white" font-size="42" font-family="sans-serif">Built for native placements.</text></svg>'
                : route.request().url().includes("/og/") ||
                    route.request().url().includes("/articles/")
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
      await context.addInitScript(() => {
        const historyKey = "devfeed:partner-placement-preferences:v1";
        const accesses = { reads: 0, writes: 0, removals: 0 };
        window.__placementHistoryAccess = accesses;
        let local;
        try {
          local = localStorage;
        } catch {
          // Fresh-document navigation also initializes an opaque about:blank page.
          return;
        }
        for (const [method, operation] of [
          ["getItem", "reads"],
          ["setItem", "writes"],
          ["removeItem", "removals"],
        ]) {
          const original = Storage.prototype[method];
          Storage.prototype[method] = function (key, ...args) {
            if (key === historyKey && this === local) accesses[operation]++;
            return original.call(this, key, ...args);
          };
        }
        local.setItem("devfeed:dev-card-promo-seen", "true");
      });
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
      await waitForImpression(page, ordinary.receipt);
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
      await page.screenshot({ path: `${reportDirectory}/${platform}-article-desktop.png` });
      await page.getByRole("link", { name: sponsored.name, exact: true }).scrollIntoViewIfNeeded();
      await waitForImpression(page, sponsored.receipt);
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
      await assertNoPlacementHistory(page, `${platform}: signed views and clicks`);
      const impressionsBeforeReload = impressionCount(ordinary.receipt);
      await page.reload();
      await page.getByRole("heading", { name: article.title, level: 1, exact: true }).waitFor();
      await page.getByRole("link", { name: sponsored.name, exact: true }).waitFor();
      await page.getByRole("link", { name: ordinary.name, exact: true }).waitFor();
      const articleQuery = queries.filter((query) => query.body.surface === "article").at(-1);
      assert.deepEqual(articleQuery.body.exclude_product_ids, []);
      await free.scrollIntoViewIfNeeded();
      await waitForImpression(page, ordinary.receipt, impressionsBeforeReload);
      await assertNoPlacementHistory(page, `${platform}: reloaded placements`);
      await page.goto(`${base}/articles/${news.slug}`);
      await page.getByRole("heading", { name: news.title, level: 1, exact: true }).waitFor();
      await page.waitForTimeout(300);
      assert.equal(
        await page.locator(".partner-placement").count(),
        0,
        `${platform}: no random fallback`,
      );
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
      const impressionsBeforeFeed = impressionCount(ordinary.receipt);
      await page.goto(`${base}/latest`);
      await assertGrid("preview background");
      const feedRequestsBeforePreview = queries.filter(
        (query) => query.body.surface === "feed",
      ).length;
      await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
      await waitForImpression(page, ordinary.receipt, impressionsBeforeFeed);
      const previewedArticle = page
        .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
        .nth(8);
      await previewedArticle.scrollIntoViewIfNeeded();
      await previewedArticle.focus();
      const beforePreview = await previewedArticle.boundingBox();
      const readingGeometry = (element) => ({
        scroll: document.scrollingElement.scrollTop,
        card: element.closest(".article-card").getBoundingClientRect().toJSON(),
        grid: element.closest(".article-grid").getBoundingClientRect().toJSON(),
        transform: getComputedStyle(element.closest(".article-card")).transform,
        products: [...document.querySelectorAll(".article-grid > .partner-placement")].map((card) =>
          card.getBoundingClientRect().toJSON(),
        ),
      });
      const geometryBeforePreview = await previewedArticle.evaluate(readingGeometry);
      await previewedArticle.evaluate((element) => {
        element.dataset.previewPreserved = "true";
      });
      const impressionsBeforePreview = impressionCount(ordinary.receipt);
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
      await waitForImpression(page, ordinary.receipt, impressionsBeforePreview);
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await page.locator(".article-modal").waitFor({ state: "hidden" });
      await assertGrid("returned feed");
      const afterPreview = await previewedArticle.boundingBox();
      if (Math.abs(afterPreview.y - beforePreview.y) > 2)
        console.error("Modal reading geometry", {
          before: geometryBeforePreview,
          after: await previewedArticle.evaluate(readingGeometry),
        });
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
      await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(1200);
      assert.equal(
        events.filter((event) => event.kind === "impression").length,
        impressionsBeforeReturn,
        `${platform}: returning to the same card does not report another impression`,
      );
      await assertNoPlacementHistory(page, `${platform}: feed and preview return`);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
      await waitForVisibleProductLogos(page);
      await page.screenshot({ path: `${reportDirectory}/${platform}-topic-mobile.png` });
      assert.ok(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        `${platform}: mobile has no horizontal overflow`,
      );
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
      await page.screenshot({ path: `${reportDirectory}/${platform}-article-mobile.png` });
      await page
        .locator(
          `.preview-sidebar .partner-placement[data-product-id="${sponsored.product_id}"] .partner-placement-label`,
        )
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `${reportDirectory}/${platform}-sponsored-pill-preview-mobile.png`,
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
            path: `${reportDirectory}/${platform}-feed-desktop.png`,
          });
        }
        if (width === 390) {
          await page.locator(".article-grid > .partner-placement").first().scrollIntoViewIfNeeded();
          await waitForVisibleProductLogos(page);
          await page.screenshot({
            path: `${reportDirectory}/${platform}-search-mobile.png`,
          });
        }
      }
      await page.setViewportSize({ width: 1280, height: 900 });
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
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
          path: `${reportDirectory}/${platform}-actual-search-${width === 390 ? "mobile" : "desktop"}.png`,
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
      for (const count of [1, 3]) {
        organicCount = count;
        for (const surface of ["grid", "search"]) {
          queries = [];
          await page.goto("about:blank");
          await page.goto(
            `${base}/${surface === "grid" ? "latest?q=typescript" : "search?q=typescript&section=articles"}`,
          );
          const selector =
            surface === "grid"
              ? ".article-grid .card-open-link"
              : ".search-section-articles .search-result-link";
          await page.locator(selector).first().waitFor();
          await page.waitForTimeout(100);
          assert.deepEqual(
            await page.locator(selector).allTextContents(),
            feedArticles.slice(0, count).map((item) => item.title),
          );
          assert.equal(
            await page.locator(".partner-placement").count(),
            0,
            `${platform}/${surface}: ${count} organic articles do not open an ad slot`,
          );
          assert.equal(
            queries.length,
            0,
            `${platform}/${surface}: sparse content reserves no inventory`,
          );
        }
      }
      organicCount = 24;
      for (const theme of ["light", "dark"]) {
        for (const width of [1280, 390]) {
          for (const mode of ["custom", "standard", "no-og"]) {
            mediaMode = mode;
            await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
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
              const image = product.querySelector("img");
              return {
                product: box(product),
                article: box(neighbor),
                hero: hero && box(hero),
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
            if (mode !== "no-og") {
              assert.ok(
                Math.abs(geometry.product.height - geometry.article.height) <= 2,
                `${platform}: ${theme} ${width} ${mode} product/article height ${geometry.product.height}/${geometry.article.height}`,
              );
              assert.ok(Math.abs(geometry.hero.height - geometry.articleHero.height) <= 1);
              assert.ok(Math.abs(geometry.hero.width / geometry.hero.height - 16 / 9) < 0.02);
            } else {
              assert.equal(
                geometry.hero,
                null,
                `${platform}: missing images do not reserve an empty hero`,
              );
              assert.ok(
                geometry.product.height < geometry.article.height * 0.75,
                `${platform}: branded card stays compact (${geometry.product.height}/${geometry.article.height})`,
              );
              const logo = await card.locator(".partner-placement-logo").boundingBox();
              assert.ok(logo.width <= 44 && logo.height <= 44);
            }
            assert.equal(geometry.radius, geometry.articleRadius);
            assert.equal(geometry.titleSize, geometry.articleTitleSize);
            assert.equal(geometry.image.fit, mode !== "no-og" ? "cover" : "contain");
            assert.ok(
              geometry.image.src.includes(
                mode === "custom"
                  ? "/ads/validator-"
                  : mode === "standard"
                    ? "/og/validator-"
                    : "/validator.svg",
              ),
              `${platform}: ${theme} ${width} uses the ${mode === "custom" ? "custom creative" : mode === "standard" ? "default OG after clearing the override" : "logo fallback"}`,
            );
            if (mode === "custom") {
              const hero = card.locator(".card-image img");
              assert.equal(await hero.getAttribute("src"), customProductImage.image_url);
              assert.equal(
                await hero.getAttribute("srcset"),
                customProductImage.image_variants
                  .map(({ url, width }) => `${url} ${width}w`)
                  .join(", "),
              );
              assert.equal(await hero.getAttribute("referrerpolicy"), "no-referrer");
              assert.equal(
                new URL(geometry.image.src).searchParams.get("v"),
                "upload-2",
                `${platform}: the selected responsive creative retains the upload cache version`,
              );
              assert.equal(await card.locator('img[src*="/og/"]').count(), 0);
            }
            assert.equal(await card.locator("time, .engagement-button").count(), 0);
            assert.ok(
              await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
            );
            await page.screenshot({
              path: `${reportDirectory}/${platform}-article-card-${theme}-${width === 390 ? "mobile" : "desktop"}-${mode}.png`,
            });
            if (mode === "standard") {
              await assertSponsoredCardInteraction(
                page,
                card,
                `${platform}: ${theme} ${width} Sponsored hover`,
                `${reportDirectory}/${platform}-sponsored-hover-${theme}-${width === 390 ? "mobile" : "desktop"}.png`,
              );
              await page
                .locator(
                  `.article-grid .partner-placement[data-product-id="${sponsored.product_id}"] .partner-placement-label`,
                )
                .scrollIntoViewIfNeeded();
              await waitForVisibleProductLogos(page);
              await page.screenshot({
                path: `${reportDirectory}/${platform}-sponsored-pill-${theme}-${width === 390 ? "mobile" : "desktop"}.png`,
              });
            }
          }
        }
      }
      for (const mode of ["broken-og", "broken-custom", "broken-custom-logo", "no-assets"]) {
        mediaMode = mode;
        await page.goto("about:blank");
        await page.goto(`${base}/topics/typescript`);
        await assertGrid(mode);
        const card = page.locator(
          `.article-grid > .partner-placement[data-product-id="${ordinary.product_id}"]`,
        );
        await card.scrollIntoViewIfNeeded();
        await waitForVisibleProductLogos(page);
        if (mode !== "no-assets") {
          assert.equal(await card.locator(".partner-placement-hero-logo").count(), 1);
          assert.equal(await card.locator(".card-image").count(), 1);
          assert.equal(
            await card.locator(".partner-placement-logo img").count(),
            mode === "broken-custom-logo" ? 0 : 1,
          );
          if (mode === "broken-custom-logo")
            assert.equal(await card.locator(".partner-placement-logo").textContent(), "TV");
        } else {
          assert.equal(await card.locator(".card-image").count(), 0);
          assert.equal(
            await card.evaluate((node) => node.classList.contains("partner-placement--branded")),
            true,
          );
          assert.equal(await card.locator(".partner-placement-logo img").count(), 0);
          assert.equal(await card.locator(".partner-placement-logo").textContent(), "TV");
        }
      }
      mediaMode = "standard";
      await page.setViewportSize({ width: 1280, height: 900 });
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
      assert.deepEqual(
        queries
          .filter((query) => query.pathname === "/v1/user/partner-placements/query")
          .map((query) => query.body.surface),
        ["article"],
        `${platform}: only the visible preview uses authenticated selection`,
      );
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        for (const [route, surface] of [
          [`/articles/${article.slug}`, "article"],
          ["/topics/typescript", "topic"],
          ["/search?q=typescript", "feed"],
          ["/?q=typescript", "feed"],
        ]) {
          queries = [];
          await page.goto(`${base}${route}`);
          // Hash navigation can reuse the current extension route and its cached cards.
          // A fresh document proves each authenticated entry selects privately.
          await page.reload();
          await page.locator(".partner-placement:visible").first().waitFor();
          assert.ok(queries.length, `${platform}: ${route} selected products`);
          assert.ok(
            queries.every((query) => query.pathname === "/v1/user/partner-placements/query"),
            `${platform}: ${route} uses the authenticated endpoint`,
          );
          assert.ok(queries.some((query) => query.body.surface === surface));
          if (surface === "feed")
            assert.ok(queries.every((query) => !query.body.allow_sponsored_fallback));
          assert.equal(
            await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
            false,
          );
        }
        await page.screenshot({
          path: `${reportDirectory}/${platform}-authenticated-context-${width === 390 ? "mobile" : "desktop"}.png`,
          animations: "disabled",
        });
        await page.goto(`${base}/settings/profile`);
        let experience = page.getByRole("combobox", { name: "Experience level", exact: true });
        await experience.waitFor();
        assert.equal(await experience.textContent(), "Not specified");
        await experience.click();
        assert.deepEqual(await page.getByRole("option").allTextContents(), [
          "Not specified",
          "Aspiring",
          "Entry-level",
          "Mid-level",
          "Senior",
        ]);
        await page.getByRole("option", { name: "Senior", exact: true }).click();
        await page.getByRole("button", { name: "Save changes", exact: true }).click();
        await page.getByText("Your profile is saved.", { exact: true }).waitFor();
        assert.equal(profileWrites.at(-1).experience_level, "senior");
        await page.reload();
        experience = page.getByRole("combobox", { name: "Experience level", exact: true });
        await experience.waitFor();
        assert.equal(await experience.textContent(), "Senior");
        assert.doesNotMatch(
          await page.locator(".dev-card-preview svg.dev-card-artwork").textContent(),
          /senior|experience_level/i,
        );
        await experience.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: `${reportDirectory}/${platform}-private-experience-${width === 390 ? "mobile" : "desktop"}.png`,
          animations: "disabled",
        });
        await experience.click();
        await page.getByRole("option", { name: "Not specified", exact: true }).click();
        await page.getByRole("button", { name: "Save changes", exact: true }).click();
        await page.getByText("Your profile is saved.", { exact: true }).waitFor();
        assert.equal(profileWrites.at(-1).experience_level, null);
      }
      signedIn = false;
      queries = [];
      // A fresh document verifies the new session without racing a hash navigation
      // against an immediate reload of the previous extension page.
      const signedOutArticle = `${base}/articles/${article.slug}`;
      await page.goto("about:blank");
      await page.goto(signedOutArticle);
      await page.getByRole("heading", { name: article.title, level: 1, exact: true }).waitFor();
      assert.equal(page.url(), signedOutArticle, `${platform}: signed-out article entry`);
      await page.locator(".partner-placement:visible").first().waitFor();
      assert.ok(
        queries.length &&
          queries.every((query) => query.pathname === "/v1/partner-placements/query"),
        `${platform}: signed-out visitors retain anonymous contextual products`,
      );
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        mediaMode = "custom";
        nativeMode = "home";
        queries = [];
        events = [];
        let releaseNative;
        deliveryGate = new Promise((resolve) => {
          releaseNative = resolve;
        });
        await page.goto(`${base}/`);
        await page.reload();
        const reading = page
          .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
          .nth(8);
        await reading.waitFor();
        // Keep the editorial card's intentional hover lift out of the reading anchor measurement.
        await page.mouse.move(0, 0);
        await reading.scrollIntoViewIfNeeded();
        await reading.focus();
        await reading.evaluate(async (node) => {
          await Promise.all(
            node
              .closest(".article-card")
              .getAnimations()
              .map((animation) => animation.finished.catch(() => {})),
          );
        });
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const readingBefore = await reading.boundingBox();
        deliveryGate = null;
        releaseNative();
        const strip = page.locator(
          '.article-placement-feed > [data-ad-format="homepage_sponsorship"]',
        );
        await strip.waitFor();
        assert.equal(
          await reading.evaluate((node) => node === document.activeElement),
          true,
          `${platform}: native delivery keeps focus`,
        );
        const readingAfter = await reading.boundingBox();
        if (Math.abs(readingAfter.y - readingBefore.y) > 2)
          console.error("Native reading geometry", platform, width, {
            before: readingBefore,
            after: readingAfter,
            state: await reading.evaluate((node) => ({
              scroll: scrollY,
              transform: getComputedStyle(node.closest(".article-card")).transform,
              top: node.closest(".article-card").getBoundingClientRect().top,
            })),
          });
        assert.ok(
          Math.abs(readingAfter.y - readingBefore.y) <= 2,
          `${platform}: native strip/content keeps reading position (${readingBefore.y} -> ${readingAfter.y})`,
        );
        assert.equal(
          await page.locator(".partner-placement:visible").count(),
          2,
          `${platform}: two total native placements`,
        );
        assert.equal(
          await strip
            .locator(
              ".card-image,.partner-placement-description,.partner-placement-destination,button",
            )
            .count(),
          0,
        );
        assert.equal(await strip.evaluate((node) => getComputedStyle(node).position), "relative");
        assert.equal(
          await strip.evaluate((node) => node.nextElementSibling.className),
          "article-grid",
        );
        assert.deepEqual(queries.at(-1).body.ad_formats, [
          "feed",
          "homepage_sponsorship",
          "sponsored_content",
          "engagement",
        ]);
        await strip.scrollIntoViewIfNeeded();
        await waitForVisibleProductLogos(page);
        assert.equal(await strip.locator("img").getAttribute("src"), ordinary.logo_url);
        await page.screenshot({
          path: `${reportDirectory}/${platform}-homepage-sponsorship-${width === 390 ? "mobile" : "desktop"}.png`,
          animations: "disabled",
        });
        const promoted = page.locator(
          '.article-grid > .article-card[data-ad-format="sponsored_content"]',
        );
        await promoted.waitFor();
        assert.equal(
          await page.getByRole("link", { name: promotedArticle.title, exact: true }).count(),
          1,
        );
        assert.equal(await promoted.locator(".card-date").count(), 1);
        assert.equal(await promoted.locator('[data-content-type="tutorial"]').count(), 1);
        assert.equal(
          await promoted.locator(".partner-placement-label").getAttribute("title"),
          `Sponsored by ${sponsored.partner_name}`,
        );
        assert.equal(
          await promoted
            .locator("button[aria-label*='Close'],.partner-placement-destination")
            .count(),
          0,
        );
        await promoted.scrollIntoViewIfNeeded();
        const editorialHero = promoted.locator(".card-image img");
        await editorialHero.waitFor();
        assert.equal(
          await editorialHero.getAttribute("src"),
          promotedArticle.image_url,
          `${platform}: sponsored content preserves its article image instead of the product creative`,
        );
        assert.equal(await promoted.locator('img[src*="/ads/"]').count(), 0);
        await page.waitForTimeout(1200);
        assert.ok(
          events.some(
            (event) => event.kind === "impression" && event.receipt === sponsored.receipt,
          ),
        );
        await page.screenshot({
          path: `${reportDirectory}/${platform}-sponsored-content-${width === 390 ? "mobile" : "desktop"}.png`,
          animations: "disabled",
        });
        await promoted.getByRole("link", { name: promotedArticle.title, exact: true }).click();
        await page
          .locator(".article-modal #article-preview-title")
          .filter({ hasText: promotedArticle.title })
          .waitFor();
        await page.waitForTimeout(100);
        assert.ok(
          events.some((event) => event.kind === "click" && event.receipt === sponsored.receipt),
        );
        assert.equal(
          await page.locator('.preview-copy > [data-promotion-kind="sponsored"]').count(),
          1,
        );
        assert.equal(
          await page
            .locator(
              '.preview-sidebar [data-ad-format="homepage_sponsorship"],.preview-sidebar [data-ad-format="sponsored_content"]',
            )
            .count(),
          0,
        );
        nativeMode = "engagement";
        queries = [];
        await page.goto(`${base}/?q=typescript`);
        await page.reload();
        const engagement = page.locator('.article-grid > [data-ad-format="engagement"]');
        await engagement.waitFor();
        assert.equal(await engagement.locator(".card-image,button").count(), 0);
        assert.equal(
          (await engagement.locator(".partner-placement-action").textContent()).trim(),
          "Explore",
        );
        assert.equal(await engagement.locator(".partner-placement-action svg").count(), 1);
        assert.equal(
          await engagement
            .locator(".partner-placement-link")
            .evaluate((node) => getComputedStyle(node).display),
          "grid",
        );
        const compact = await engagement.boundingBox();
        assert.ok(compact.height < 220, `${platform}: engagement stays compact at ${width}px`);
        await engagement.locator(".partner-placement-link").focus();
        assert.equal(
          await engagement
            .locator(".partner-placement-link")
            .evaluate((node) => node.matches(":focus-visible")),
          true,
        );
        assert.equal(
          await engagement.locator(".partner-placement-label").textContent(),
          "Sponsored",
        );
        assert.equal(await page.locator('[data-ad-format="homepage_sponsorship"]').count(), 0);
        assert.ok(
          queries.every(
            (query) =>
              !query.body.allow_sponsored_fallback &&
              !query.body.ad_formats.includes("homepage_sponsorship"),
          ),
        );
        await engagement.scrollIntoViewIfNeeded();
        await waitForVisibleProductLogos(page);
        assert.equal(await engagement.locator("img").getAttribute("src"), ordinary.logo_url);
        await page.screenshot({
          path: `${reportDirectory}/${platform}-engagement-${width === 390 ? "mobile" : "desktop"}.png`,
          animations: "disabled",
        });
        nativeMode = "disclosure";
        await page.goto(`${base}/`);
        await page.reload();
        const permanent = page.locator(".article-card:not(.partner-placement)").first();
        await permanent.locator('[data-promotion-kind="sponsored"]').waitFor();
        assert.equal(await page.locator(".partner-placement").count(), 0);
        assert.equal(
          await permanent.locator('[data-promotion-kind="sponsored"]').getAttribute("title"),
          `Sponsored by ${sponsored.partner_name}`,
        );
        await permanent.locator(".card-open-link").click();
        await page.locator('.preview-copy > [data-promotion-kind="sponsored"]').waitFor();
        assert.equal(await page.locator(".partner-placement").count(), 0);
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
          false,
        );
      }
      nativeMode = null;
      mediaMode = "standard";
      placementLimited = true;
      try {
        const limited = await page.evaluate(
          async (url) => {
            const response = await fetch(url, {
              method: "POST",
              credentials: "omit",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ surface: "feed" }),
            });
            return {
              status: response.status,
              retryAfter: response.headers.get("retry-after"),
              cacheControl: response.headers.get("cache-control"),
              body: await response.json(),
            };
          },
          `${extension ? "https://devfeed.tech" : origin}/api/v1/partner-placements/query`,
        );
        const { cacheControl, ...retryGuidance } = limited;
        assert.ok(
          cacheControl?.split(",").some((directive) => directive.trim() === "no-store"),
          `${platform}: throttled placement responses cannot be cached`,
        );
        assert.deepEqual(
          retryGuidance,
          {
            status: 429,
            retryAfter: "47",
            body: { detail: "Partner recommendations unavailable" },
          },
          `${platform}: real gateway preserves placement retry guidance and sanitizes errors`,
        );
      } finally {
        placementLimited = false;
      }
      await assertNoPlacementHistory(page, `${platform}: reader navigation`);
      await assertMountedPlacementExpiry(context, base, platform);
      for (const pathname of ["/v1/partner-placements/query", "/v1/partner-tracking/events"])
        assert.ok(
          observedCallerProofs.get(pathname)?.has(callerFixtureIps[platform]),
          `${platform}: production gateway preserves distinct caller identity for ${pathname}`,
        );
      console.log(
        `${platform}: built web/extension in-grid homepage, search, category and topic cards; managed custom product creatives, clearing to default OG, responsive sources and failed-image fallback; compact formats preserve logos and sponsored content preserves its article image; every product disclosed with distinct amber Sponsored pills and no dismissal; branded preview sidebar; focus/layout stability; signed campaign and non-campaign tracking; configured production gateway caller proofs, credential isolation and placement429 retry guidance; mounted receipt expiry and background wake preserve reading position without refreshing or tracking; viewable placements never read, write or remove abandoned local history; private session routing; optional private experience save/reload/clear; article priority passed`,
      );
    } catch (error) {
      const page = context.pages().findLast((page) => page.url() !== "about:blank");
      console.error("Placement browser failure", platform, {
        signedIn,
        nativeMode,
        queries,
        browserErrors: browserErrors.slice(-20),
        pages: context.pages().map((page) => page.url()),
        page: page
          ? {
              url: page.url(),
              text: await page.locator("body").innerText(),
              placements: await page
                .locator(".partner-placement")
                .evaluateAll((nodes) => nodes.map((node) => node.outerHTML)),
            }
          : null,
      });
      await page?.screenshot({ path: `${reportDirectory}/${platform}-failure.png` });
      throw error;
    } finally {
      await context.close();
    }
  }
  assert.equal(
    observedCallerIps.size,
    3,
    "The gateway keeps all three anonymous reader callers distinct",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  app.kill("SIGTERM");
  edge.closeAllConnections();
  await new Promise((resolve) => edge.close(resolve));
  await new Promise((resolve) => fixture.close(resolve));
  await rm(temp, { recursive: true, force: true });
}
