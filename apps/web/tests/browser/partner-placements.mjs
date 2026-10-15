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
  tagline: "Validate application inputs.",
  url: "https://partner.example/validator",
  logo_url: "https://assets.partner.example/validator.svg",
  image_url: "https://assets.partner.example/og/validator-original.svg",
  image_variants: [
    { url: "https://assets.partner.example/og/validator-400.svg", width: 400 },
    { url: "https://assets.partner.example/og/validator-800.svg", width: 800 },
  ],
  partner_name: "Free partner",
  platform: {
    name: "Developer Launchpad",
    logo_url: "https://assets.partner.example/platforms/launchpad.svg",
    logo_variants: [
      { url: "https://assets.partner.example/platforms/launchpad-32.svg", width: 32 },
      { url: "https://assets.partner.example/platforms/launchpad-64.svg", width: 64 },
    ],
  },
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
let headerQueries = [];
let headerInventory = false;
let events = [];
let deliveryGate = null;
let mediaMode = "standard";
let platformMode = "managed";
let platformLogoGate = null;
let experienceLevel = null;
let profileWrites = [];
let nativeMode = null;
let organicCount = 24;
let promotedArrival = false;
let organicPagesDelivered = 0;
let placementLimited = false;
let duplicateCollectionProduct = false;
let isolateArticleReceipts = false;
let articleReceiptSequence = 0;
const acceptedTrackingReceipts = new Set([ordinary.receipt, sponsored.receipt]);
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

async function assertPlacementPlatform(card, { mode = "managed", label }) {
  const source = card.locator(".card-source");
  const icon = source.locator(".source-avatar");
  await icon.waitFor();
  assert.equal(
    (await source.textContent()).trim(),
    mode === "manual" ? ordinary.partner_name : ordinary.platform.name,
    `${label}: attribution uses the selected platform or the manual product's partner`,
  );
  if (mode === "managed") {
    const image = icon.locator("img");
    await image.waitFor();
    await image.evaluate((element) => element.decode());
    assert.equal(await image.getAttribute("src"), ordinary.platform.logo_url);
    assert.equal(await image.getAttribute("alt"), "");
    assert.equal(await image.getAttribute("referrerpolicy"), "no-referrer");
    assert.equal(
      await image.getAttribute("srcset"),
      ordinary.platform.logo_variants.map(({ url, width }) => `${url} ${width}w`).join(", "),
    );
    const rendered = await image.boundingBox();
    assert.equal(rendered.width, 12, `${label}: platform logos match organic source icons`);
    assert.equal(rendered.height, 12);
  } else {
    await icon.locator("svg").waitFor();
    assert.equal(
      await icon.locator("img").count(),
      0,
      `${label}: unavailable logos use a fallback`,
    );
  }
  const geometry = await source.evaluate((element) => {
    const rect = (node) => node.getBoundingClientRect().toJSON();
    return {
      source: rect(element),
      icon: rect(element.querySelector(".source-avatar")),
      badge: rect(element.parentElement.querySelector(".partner-placement-label")),
    };
  });
  assert.equal(geometry.icon.width, 16, `${label}: the source icon keeps its reserved width`);
  assert.equal(geometry.icon.height, 16);
  assert.ok(
    Math.abs(
      geometry.icon.y + geometry.icon.height / 2 - geometry.source.y - geometry.source.height / 2,
    ) <= 2,
    `${label}: icon and platform name share a baseline`,
  );
  assert.ok(geometry.source.right <= geometry.badge.x + 1, `${label}: disclosure stays separate`);
}

async function assertPlatformFallbacks(page, base, platform) {
  mediaMode = "custom";
  try {
    for (const theme of ["light", "dark"]) {
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        for (const mode of ["missing", "broken", "manual"]) {
          platformMode = mode;
          let release;
          if (mode === "broken")
            platformLogoGate = new Promise((resolve) => {
              release = resolve;
            });
          try {
            await page.evaluate((value) => localStorage.setItem("devfeed:theme", value), theme);
            await page.goto("about:blank");
            await page.goto(`${base}/?q=typescript`);
            const card = page.locator(
              `.article-grid > .partner-placement[data-product-id="${ordinary.product_id}"]`,
            );
            await card.waitFor();
            await card.scrollIntoViewIfNeeded();
            await page.mouse.move(0, 0);
            const before = await card.evaluate((element) => ({
              card: element.getBoundingClientRect().toJSON(),
              source: element.querySelector(".card-meta-row").getBoundingClientRect().toJSON(),
            }));
            platformLogoGate = null;
            release?.();
            const label = `${platform}/${theme}/${width}: ${mode} platform logo`;
            await assertPlacementPlatform(card, { mode, label });
            const after = await card.evaluate((element) => ({
              card: element.getBoundingClientRect().toJSON(),
              source: element.querySelector(".card-meta-row").getBoundingClientRect().toJSON(),
            }));
            assert.ok(Math.abs(before.card.height - after.card.height) <= 2);
            assert.ok(Math.abs(before.source.height - after.source.height) <= 1);
            assert.ok(
              await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
              `${label}: the fallback stays within the native card`,
            );
            await page.screenshot({
              path: `${reportDirectory}/${platform}-platform-logo-${mode}-${theme}-${width}.png`,
            });
          } finally {
            platformLogoGate = null;
            release?.();
          }
        }
      }
    }
  } finally {
    platformMode = "managed";
    platformLogoGate = null;
  }
}

async function assertCompactSidebar(page, selector, label, expected) {
  const sidebar = page.locator(selector);
  const placements = sidebar.locator(".partner-placement");
  assert.equal(await placements.count(), expected, `${label}: compact placement count`);
  assert.deepEqual(
    await placements.evaluateAll((cards) => cards.map((card) => card.dataset.adFormat)),
    Array(expected).fill("engagement"),
    `${label}: sidebars use Engagement inventory only`,
  );
  assert.equal(
    await sidebar
      .locator('.card-image,[data-ad-format="feed"],[data-ad-format="sponsored_content"]')
      .count(),
    0,
    `${label}: no Feed artwork or sponsored article cards in the sidebar`,
  );
  for (const card of await placements.all()) {
    const geometry = await card.boundingBox();
    assert.ok(geometry.height < 220, `${label}: Engagement stays compact`);
    assert.equal(await card.locator("button").count(), 0);
    assert.equal(
      await card.locator(".partner-placement-action,.partner-placement-destination svg").count(),
      0,
      `${label}: Engagement has no extra action text or arrow`,
    );
    assert.equal(
      (await card.locator(".partner-placement-destination").textContent()).trim(),
      "partner.example",
      `${label}: the linked card retains its destination host`,
    );
    assert.equal(await card.locator(".partner-placement-label").textContent(), "Sponsored");
  }
  if (await sidebar.evaluate((node) => node.matches(".preview-sidebar"))) {
    await sidebar.locator(".topic-brief:not(.source-brief)").waitFor();
    const order = await sidebar.evaluate((node) => {
      const briefs = [...node.querySelectorAll(":scope > .topic-brief")];
      const firstPlacement = node.querySelector(".partner-placement");
      return {
        contextSections: briefs.length,
        followsContext: briefs.every((brief) =>
          Boolean(brief.compareDocumentPosition(firstPlacement) & Node.DOCUMENT_POSITION_FOLLOWING),
        ),
        contextBottom: Math.max(...briefs.map((brief) => brief.getBoundingClientRect().bottom)),
        placementTop: firstPlacement.getBoundingClientRect().top,
      };
    });
    assert.equal(order.contextSections, 2, `${label}: source and topic context remain present`);
    assert.equal(
      order.followsContext,
      true,
      `${label}: Engagement follows source and topic context`,
    );
    assert.ok(
      order.placementTop >= order.contextBottom + 23,
      `${label}: Engagement has breathing room below the topic section`,
    );
  }
}

async function assertFeedPlacementContinuity(page, base, platform) {
  const originalSignedIn = signedIn;
  isolateArticleReceipts = true;
  await page.setViewportSize({ width: 1280, height: 900 });
  try {
    for (const authenticated of [false, true]) {
      signedIn = authenticated;
      for (const promotedArticleEntry of [false, true]) {
        const label = `${platform}/${authenticated ? "member" : "guest"}/${promotedArticleEntry ? "sponsored article" : "article"}`;
        nativeMode = promotedArticleEntry ? "sponsored-content" : null;
        mediaMode = "custom";
        queries = [];
        events = [];
        await page.goto(`${base}/latest`);
        await page.reload();
        const selector = ".article-grid > .partner-placement";
        const cards = page.locator(selector);
        await cards.first().waitFor();
        assert.equal(await cards.count(), promotedArticleEntry ? 1 : 2, label);
        const receipts = promotedArticleEntry
          ? [sponsored.receipt]
          : [ordinary.receipt, sponsored.receipt];
        for (let index = 0; index < receipts.length; index++) {
          await cards.nth(index).scrollIntoViewIfNeeded();
          await waitForImpression(page, receipts[index]);
        }
        await page.mouse.move(0, 0);
        await waitForVisibleProductLogos(page);
        const beforeImpressions = receipts.map(impressionCount);
        const beforeFeedRequests = queries.filter((query) => query.body.surface === "feed").length;
        await page.evaluate((selector) => {
          const saved = [...document.querySelectorAll(selector)].map((card) => {
            const image = card.querySelector(".card-image img, .partner-placement-logo img");
            const link = card.querySelector(".partner-placement-link, .card-open-link");
            const bounds = card.getBoundingClientRect();
            return {
              card,
              parent: card.parentElement,
              image,
              link,
              href: link.getAttribute("href"),
              source: image?.getAttribute("src"),
              sources: image?.getAttribute("srcset"),
              width: bounds.width,
              height: bounds.height,
            };
          });
          const state = { saved, frames: 0, failures: [], running: true };
          window.__placementContinuity = state;
          const inspect = () => {
            if (!state.running) return;
            state.frames++;
            for (const item of saved) {
              const { card, image, link } = item;
              const style = getComputedStyle(card);
              const bounds = card.getBoundingClientRect();
              if (
                !card.isConnected ||
                card.parentElement !== item.parent ||
                card.querySelector(".partner-placement-link, .card-open-link") !== link ||
                card.querySelector(".card-image img, .partner-placement-logo img") !== image ||
                link.getAttribute("href") !== item.href ||
                image?.getAttribute("src") !== item.source ||
                image?.getAttribute("srcset") !== item.sources ||
                card.hidden ||
                style.display === "none" ||
                style.visibility !== "visible" ||
                Math.abs(bounds.width - item.width) > 1 ||
                Math.abs(bounds.height - item.height) > 1
              ) {
                if (!state.failures.length)
                  state.failures.push({
                    product: card.dataset.productId,
                    connected: card.isConnected,
                    display: style.display,
                    visibility: style.visibility,
                    suspended: card.dataset.suspended,
                    width: bounds.width,
                    height: bounds.height,
                  });
              }
            }
            requestAnimationFrame(inspect);
          };
          requestAnimationFrame(inspect);
        }, selector);
        const articleLink = promotedArticleEntry
          ? cards.first().getByRole("link", { name: promotedArticle.title, exact: true })
          : page
              .locator(".article-grid > .article-card:not(.partner-placement) .card-open-link")
              .nth(8);
        const articleTitle = (await articleLink.textContent()).trim();
        await articleLink.click();
        await page.locator(".article-modal[open]").waitFor();
        await page
          .locator(".article-modal #article-preview-title")
          .filter({ hasText: articleTitle })
          .waitFor();
        await page.waitForFunction(
          (selector) =>
            [...document.querySelectorAll(selector)].every(
              (card) => card.getAttribute("aria-hidden") === "true" && card.hasAttribute("inert"),
            ),
          selector,
        );
        await page.waitForTimeout(1200);
        assert.deepEqual(
          receipts.map(impressionCount),
          beforeImpressions,
          `${label}: the covered feed never records impressions while an article owns tracking`,
        );
        if (authenticated) await page.goBack();
        else await page.getByRole("button", { name: "Close preview", exact: true }).click();
        await page.locator(".article-modal").waitFor({ state: "hidden" });
        await cards.first().waitFor();
        await page.waitForFunction(
          (selector) =>
            [...document.querySelectorAll(selector)].every(
              (card) => !card.hasAttribute("inert") && !card.hasAttribute("aria-hidden"),
            ),
          selector,
        );
        await cards.first().scrollIntoViewIfNeeded();
        await page.waitForTimeout(1200);
        const continuity = await page.evaluate(() => {
          const state = window.__placementContinuity;
          state.running = false;
          return { frames: state.frames, failures: state.failures };
        });
        assert.ok(continuity.frames > 2, `${label}: real animation frames were observed`);
        assert.deepEqual(
          continuity.failures,
          [],
          `${label}: original sponsored cells never flicker`,
        );
        assert.equal(
          queries.filter((query) => query.body.surface === "feed").length,
          beforeFeedRequests,
          `${label}: returning from an article reuses the original feed delivery`,
        );
        assert.deepEqual(
          receipts.map(impressionCount),
          beforeImpressions,
          `${label}: returning resumes original receipts without duplicate impressions`,
        );
        const receipt = receipts[0];
        const clicksBefore = events.filter(
          (event) => event.kind === "click" && event.receipt === receipt,
        ).length;
        const restoredLink = cards.first().locator(".partner-placement-link, .card-open-link");
        await restoredLink.evaluate((node) => {
          node.addEventListener("click", (event) => event.preventDefault(), { once: true });
        });
        await restoredLink.click();
        await page.waitForTimeout(100);
        assert.equal(
          events.filter((event) => event.kind === "click" && event.receipt === receipt).length,
          clicksBefore + Number(!promotedArticleEntry),
          `${label}: retained tracking uses its original receipt and deduplicates prior clicks`,
        );
      }
    }
  } finally {
    await page.evaluate(() => {
      if (window.__placementContinuity) window.__placementContinuity.running = false;
    });
    signedIn = originalSignedIn;
    isolateArticleReceipts = false;
    nativeMode = null;
    mediaMode = "standard";
  }
}

async function assertSitewideHeader(context, base, platform) {
  const previousSignedIn = signedIn;
  headerInventory = true;
  signedIn = true;
  const page = await context.newPage();
  const selector = ".topbar .partner-placement--header_sponsorship";
  const header = page.locator(selector);
  const inspect = async (label) => {
    await header.waitFor();
    assert.equal(await header.count(), 1, `${label}: exactly one Header sponsorship`);
    assert.equal(
      await header.locator(".partner-placement-title > strong").textContent(),
      headerSponsor.name,
    );
    assert.equal(
      await header.locator(".partner-placement-description").textContent(),
      headerSponsor.tagline,
    );
    assert.equal(await header.locator(".partner-placement-label").textContent(), "Sponsored");
    assert.equal(
      await header.locator(".card-image,.partner-placement-destination,button").count(),
      0,
    );
    assert.equal(
      await page.locator('.main-content [data-ad-format="header_sponsorship"]').count(),
      0,
    );
    const geometry = await header.evaluate((node) => {
      const bounds = (element) => element.getBoundingClientRect().toJSON();
      const shell = node.closest(".topbar");
      return {
        credit: bounds(node),
        shell: bounds(shell),
        peers: [
          shell.querySelector(".brand"),
          shell.querySelector(".search"),
          shell.querySelector(".header-actions"),
        ].map(bounds),
        overflow: document.documentElement.scrollWidth > innerWidth,
      };
    });
    assert.equal(geometry.overflow, false, `${label}: no page overflow`);
    assert.ok(
      geometry.credit.height <= 48 && geometry.shell.height <= 80,
      `${label}: no extra header row`,
    );
    for (const peer of geometry.peers)
      assert.ok(
        geometry.credit.right <= peer.x + 1 ||
          geometry.credit.x >= peer.right - 1 ||
          geometry.credit.bottom <= peer.y + 1 ||
          geometry.credit.y >= peer.bottom - 1,
        `${label}: credit never overlaps navigation`,
      );
  };
  try {
    // Every route starts as a real document so delivery cannot accidentally depend
    // on a previously visited feed or an already selected Feed card.
    for (const [width, theme] of [
      [1440, "light"],
      [1906, "dark"],
    ]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((theme) => localStorage.setItem("devfeed:theme", theme), theme);
      for (const [path, marker] of [
        ["/latest", ".article-grid .card-open-link"],
        ["/search?q=typescript&section=topics", ".search-section-topics .search-result-link"],
        ["/topics", ".topic-card-link"],
        ["/topics/typescript", ".collection-feed-articles .card-open-link"],
        ["/sources/engineering", ".collection-feed-articles .card-open-link"],
        [`/articles/${article.slug}`, ".article-modal #article-preview-title"],
        ["/read-later", ".article-grid .card-open-link"],
        ["/users/fixture-reader", ".public-profile h1"],
        ["/settings/profile", ".profile-settings-nav"],
        ["/settings/feed", '[aria-label="Feed preferences"]'],
      ]) {
        await page.goto("about:blank");
        headerQueries = [];
        await page.goto(`${base}${path}`);
        await page.locator(marker).first().waitFor();
        if (path.startsWith("/articles/")) {
          const before = impressionCount(headerSponsor.receipt);
          await page.waitForTimeout(1200);
          assert.equal(
            headerQueries.length,
            0,
            `${platform}: direct article defers covered Header inventory`,
          );
          assert.equal(
            impressionCount(headerSponsor.receipt),
            before,
            `${platform}: covered Header earns no impression`,
          );
          assert.equal(await header.count(), 0);
          await page.screenshot({
            path: `${reportDirectory}/${platform}-header-covered-direct-article-${theme}.png`,
            animations: "disabled",
          });
          await page.getByRole("button", { name: "Close preview", exact: true }).click();
          await page.locator(".article-modal[open]").waitFor({ state: "detached" });
        }
        await inspect(`${platform}/${theme}/${path}`);
        assert.equal(headerQueries.length, 1, `${path}: one independent Header selection`);
        assert.equal(headerQueries[0].pathname, "/v1/user/partner-placements/query");
        await page.screenshot({
          path: `${reportDirectory}/${platform}-header-sitewide-${path.split("?")[0].replaceAll("/", "-").slice(1)}-${theme}.png`,
          animations: "disabled",
        });
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("about:blank");
    headerQueries = [];
    const previous = impressionCount(headerSponsor.receipt);
    await page.goto(`${base}/latest`);
    await inspect(`${platform}: initial persistent Header`);
    await header.evaluate((node) => {
      window.__persistentHeader = node;
      window.__persistentHeaderLink = node.querySelector(".partner-placement-link");
    });
    await waitForImpression(page, headerSponsor.receipt, previous);
    const impressions = impressionCount(headerSponsor.receipt);
    const selections = headerQueries.length;
    const continuity = async (label) => {
      await inspect(label);
      assert.equal(
        await header.evaluate(
          (node) =>
            node === window.__persistentHeader &&
            node.querySelector(".partner-placement-link") === window.__persistentHeaderLink,
        ),
        true,
        `${label}: original credit and signed link DOM stay mounted`,
      );
      assert.equal(headerQueries.length, selections, `${label}: navigation does not select again`);
      assert.equal(
        impressionCount(headerSponsor.receipt),
        impressions,
        `${label}: navigation does not repeat the receipt impression`,
      );
    };
    await page.getByRole("link", { name: "Read later", exact: true }).first().click();
    await page.getByRole("heading", { name: "Read later", exact: true }).waitFor();
    await continuity(`${platform}: saved articles`);
    await page.getByRole("link", { name: "Explore topics", exact: true }).first().click();
    await page.locator(".topic-card-link").first().waitFor();
    await continuity(`${platform}: topic catalog`);
    await page.locator(".topic-card-link").first().click();
    await page.locator(".collection-feed-articles .card-open-link").first().waitFor();
    await continuity(`${platform}: topic detail`);
    await page
      .locator("a.nav-item")
      .filter({ hasText: /^Sources$/ })
      .click();
    await page.locator(".source-card-link").first().waitFor();
    await page.locator(".source-card-link").first().click();
    await page.locator(".collection-feed-articles .card-open-link").first().waitFor();
    await continuity(`${platform}: source detail`);
    await page.locator(".topbar .search input").fill("typescript");
    await page.locator(".search-section-articles .search-result-link").first().waitFor();
    await continuity(`${platform}: search results`);
    await page.locator(".search-section-articles .search-result-link").first().click();
    await page.locator(".article-modal[open]").waitFor();
    assert.equal(
      await header.evaluate((node) => node === window.__persistentHeader),
      true,
      `${platform}: opening article retains Header DOM`,
    );
    assert.equal(headerQueries.length, selections);
    assert.equal(
      await header.getAttribute("aria-hidden"),
      "true",
      `${platform}: modal removes covered Header from accessibility tree`,
    );
    assert.equal(await header.evaluate((node) => node.hasAttribute("inert")), true);
    assert.equal(await header.isVisible(), true, `${platform}: covered Header keeps its pixels`);
    const coveredEvents = events.filter((event) => event.receipt === headerSponsor.receipt).length;
    await header.locator(".partner-placement-link").evaluate((link) => {
      link.addEventListener("click", (event) => event.preventDefault(), { once: true });
      link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await page.waitForTimeout(1200);
    assert.equal(
      events.filter((event) => event.receipt === headerSponsor.receipt).length,
      coveredEvents,
      `${platform}: covered Header stops signed measurements`,
    );
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.locator(".article-modal[open]").waitFor({ state: "detached" });
    await continuity(`${platform}: article close`);
    await page.locator(".search-section-articles .search-result-link").first().click();
    await page.locator(".article-modal[open]").waitFor();
    await page.goBack();
    await page.locator(".article-modal[open]").waitFor({ state: "detached" });
    await continuity(`${platform}: article Back`);
    await page.getByRole("button", { name: "User menu: Reader", exact: true }).click();
    await page.getByRole("menuitem", { name: "Your Dev Card", exact: true }).click();
    await page.locator(".profile-settings-nav").waitFor();
    await continuity(`${platform}: profile settings`);
    await page
      .getByRole("navigation", { name: "Settings sections" })
      .getByRole("link", { name: "Feed", exact: true })
      .click();
    await page.getByRole("region", { name: "Feed preferences" }).waitFor();
    await continuity(`${platform}: feed settings`);
    await page.getByRole("button", { name: "User menu: Reader", exact: true }).click();
    await page.getByRole("menuitem", { name: "Your Profile", exact: true }).click();
    await page.locator(".public-profile h1").waitFor();
    await continuity(`${platform}: public profile`);
    const clickCount = events.filter(
      (event) => event.receipt === headerSponsor.receipt && event.kind === "click",
    ).length;
    const popupEvent = context.waitForEvent("page");
    await header.locator(".partner-placement-link").click();
    await (await popupEvent).close();
    await page.waitForTimeout(100);
    assert.equal(
      events.filter((event) => event.receipt === headerSponsor.receipt && event.kind === "click")
        .length,
      clickCount + 1,
      `${platform}: same Header receipt records the eventual signed click`,
    );
    for (const width of [1279, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("about:blank");
      headerQueries = [];
      await page.goto(`${base}/settings/feed`);
      await page.getByRole("region", { name: "Feed preferences" }).waitFor();
      assert.equal(
        await header.count(),
        0,
        `${platform}/${width}: narrow headers hide sponsorship`,
      );
      assert.equal(
        headerQueries.length,
        0,
        `${platform}/${width}: hidden Header reserves no delivery`,
      );
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      await page.screenshot({
        path: `${reportDirectory}/${platform}-header-sitewide-narrow-${width}.png`,
        animations: "disabled",
      });
    }
    signedIn = false;
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("about:blank");
    headerQueries = [];
    await page.goto(`${base}/topics`);
    await inspect(`${platform}: anonymous catalog Header`);
    assert.equal(headerQueries.length, 1);
    assert.equal(
      headerQueries[0].pathname,
      "/v1/partner-placements/query",
      `${platform}: signed-out Header uses public transport`,
    );
    await assertNoPlacementHistory(page, `${platform}: sitewide Header`);
  } finally {
    await page.close();
    headerInventory = false;
    signedIn = previousSignedIn;
  }
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
  await assertSearchSidebarExpiry(context, base, platform);
  await assertCompactFeedTable(context, base, platform);
  await assertHeaderExpiry(context, base, platform);
}

async function assertHeaderExpiry(context, base, platform) {
  const previousSignedIn = signedIn;
  signedIn = true;
  headerInventory = true;
  try {
    for (const mode of ["foreground", "background-wake"]) {
      await context.clock.setSystemTime(receiptIssuedAt * 1000);
      const page = await context.newPage();
      await page.setViewportSize({ width: 1440, height: 1000 });
      try {
        headerQueries = [];
        await page.goto(`${base}/settings/feed`);
        const header = page.locator(".topbar .partner-placement--header_sponsorship");
        await header.waitFor();
        const reading = page.getByRole("radio", { name: "Cards", exact: true });
        await reading.waitFor();
        await reading.focus();
        await header.evaluate((node) => {
          window.__expiringHeader = node;
        });
        await page.clock.fastForward(59 * 60_000);
        assert.equal(
          await header.isVisible(),
          true,
          `${platform}/${mode}: Header receipt is valid before deadline`,
        );
        const before = await reading.boundingBox();
        const shell = await page.locator(".topbar").boundingBox();
        const requests = headerQueries.length;
        const measurements = events.filter(
          (event) => event.receipt === headerSponsor.receipt,
        ).length;
        if (mode === "foreground") await page.clock.fastForward(61_000);
        else {
          await page.evaluate(() =>
            Object.defineProperty(document, "visibilityState", {
              configurable: true,
              value: "hidden",
            }),
          );
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
          const node = window.__expiringHeader;
          return !node.isConnected || node.hidden || getComputedStyle(node).visibility === "hidden";
        });
        assert.equal(
          await page.getByRole("link", { name: headerSponsor.name, exact: true }).count(),
          0,
          `${platform}/${mode}: expired Header cannot be activated`,
        );
        assert.equal(
          headerQueries.length,
          requests,
          `${platform}/${mode}: expiry does not rotate global sponsorship`,
        );
        const after = await reading.boundingBox();
        assert.ok(
          Math.abs(after.y - before.y) <= 2,
          `${platform}/${mode}: Header expiry keeps the settings focus position`,
        );
        assert.equal(await reading.evaluate((node) => node === document.activeElement), true);
        assert.equal((await page.locator(".topbar").boundingBox()).height, shell.height);
        await page.evaluate(() => {
          const link = window.__expiringHeader.querySelector("a");
          link.addEventListener("click", (event) => event.preventDefault(), { once: true });
          link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        });
        await page.clock.runFor(1200);
        assert.equal(
          events.filter((event) => event.receipt === headerSponsor.receipt).length,
          measurements,
          `${platform}/${mode}: stale Header receipt cannot emit tracking`,
        );
        await page.screenshot({
          path: `${reportDirectory}/${platform}-header-expiry-${mode}.png`,
          animations: "disabled",
        });
      } finally {
        await page.close();
      }
    }
  } finally {
    signedIn = previousSignedIn;
    headerInventory = false;
  }
}

async function assertOrganicRestoration(context, base, platform) {
  await context.clock.setSystemTime(receiptIssuedAt * 1000);
  nativeMode = "sponsored-content";
  promotedArrival = true;
  const delivered = organicPagesDelivered;
  const page = await context.newPage();
  await page.addInitScript(() => {
    sessionStorage.setItem("devfeed:dev-card-promo-dismissed", "true");
  });
  try {
    await page.goto(`${base}/latest?q=typescript`);
    const promoted = page.locator('[data-ad-format="sponsored_content"]');
    await promoted.waitFor();
    await page.locator('.pagination[data-has-more="true"]').first().scrollIntoViewIfNeeded();
    await page.locator('.pagination[data-has-more="false"]').first().waitFor({ state: "attached" });
    assert.equal(
      organicPagesDelivered,
      delivered + 1,
      `${platform}/grid: a later page really returns the promoted article organically`,
    );
    assert.equal(
      await page.getByRole("link", { name: promotedArticle.title, exact: true }).count(),
      1,
    );
    const selector = ".article-grid > .article-card:not(.partner-placement) .card-open-link";
    // Wait for the article list itself to settle after pagination before
    // measuring layout; the pagination marker alone does not assert its contents.
    await page.waitForFunction(
      (selector) => document.querySelectorAll(selector).length === 24,
      selector,
    );
    assert.deepEqual(
      await page.locator(selector).allTextContents(),
      feedArticles.map((item) => item.title),
      `${platform}/grid: pagination retains the original articles exactly once`,
    );
    const reading = page.locator(selector).nth(8);
    await reading.scrollIntoViewIfNeeded();
    await reading.focus();
    await reading.evaluate((node) => node.setAttribute("data-organic-restoration-anchor", "true"));
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
      `${platform}/grid: expiry restores the organic article exactly once in page order`,
    );
    const after = await reading.boundingBox();
    assert.ok(
      Math.abs(after.y - before.y) <= 2,
      `${platform}/grid: organic restoration preserves viewport reading position`,
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
      `${platform}/grid: organic restoration does not request new placements`,
    );
    await assertNoPlacementHistory(page, `${platform}/grid: organic restoration`);
    await page.screenshot({
      path: `${reportDirectory}/${platform}-grid-organic-restoration.png`,
    });
  } finally {
    await page.close();
    promotedArrival = false;
    nativeMode = null;
  }
}

async function assertSearchSidebarExpiry(context, base, platform) {
  nativeMode = null;
  for (const width of [1280, 390]) {
    await context.clock.setSystemTime(receiptIssuedAt * 1000);
    const page = await context.newPage();
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    try {
      const previousImpressions = impressionCount(ordinary.receipt);
      await page.goto(`${base}/search?q=typescript`);
      const card = page.locator('.search-sidebar [data-ad-format="engagement"]');
      await card.waitFor();
      await card.scrollIntoViewIfNeeded();
      await waitForImpression(page, ordinary.receipt, previousImpressions);
      const reading = page.locator(".search-section-topics .search-result-link").first();
      await reading.scrollIntoViewIfNeeded();
      await reading.focus();
      await reading.evaluate((node) => {
        window.__searchExpiryAnchor = node;
      });
      const before = await reading.boundingBox();
      const cardBefore = await card.boundingBox();
      const beforeQueries = queries.length;
      const beforeEvents = events.length;
      await page.clock.fastForward(60 * 60_000 + 1000);
      await page.waitForFunction(
        () =>
          document.querySelector(".search-sidebar .partner-placement")?.dataset.suspended ===
          "true",
      );
      assert.equal(
        await card.isVisible(),
        false,
        `${platform}/${width}: expired sidebar creative is hidden`,
      );
      const cardAfter = await card.evaluate((node) => node.getBoundingClientRect().toJSON());
      assert.ok(
        Math.abs(cardAfter.height - cardBefore.height) <= 1,
        `${platform}/${width}: expiry retains sidebar geometry`,
      );
      const after = await reading.boundingBox();
      assert.ok(
        Math.abs(after.y - before.y) <= 2,
        `${platform}/${width}: expiry preserves the related result being read`,
      );
      assert.equal(
        await reading.evaluate(
          (node) => node === window.__searchExpiryAnchor && node === document.activeElement,
        ),
        true,
      );
      assert.deepEqual(
        await page.locator(".search-section-articles .search-result-link").allTextContents(),
        feedArticles.map((item) => item.title),
      );
      await card.locator(".partner-placement-link").evaluate((link) => {
        link.addEventListener("click", (event) => event.preventDefault(), { once: true });
        link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      });
      await page.clock.runFor(2000);
      assert.equal(
        queries.length,
        beforeQueries,
        `${platform}/${width}: expiry never refreshes search inventory`,
      );
      assert.equal(
        events.length,
        beforeEvents,
        `${platform}/${width}: expired sidebar stops measurements`,
      );
      await page.screenshot({
        path: `${reportDirectory}/${platform}-search-sidebar-expiry-${width}.png`,
      });
    } finally {
      await page.close();
    }
  }
}

async function assertCollectionFeedSidebars(page, base, platform) {
  const selector = ".collection-feed-sidebar";
  nativeMode = null;
  mediaMode = "standard";
  try {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      for (const kind of ["topic", "source"]) {
        const route = kind === "topic" ? "/topics/typescript" : "/sources/engineering";
        const label = `${platform}/${width}: ${kind} detail`;
        queries = [];
        let release;
        deliveryGate = new Promise((resolve) => {
          release = resolve;
        });
        try {
          await page.goto("about:blank");
          await page.goto(`${base}${route}`);
          const reading = page.locator(".collection-feed-articles .card-open-link").nth(8);
          await reading.waitFor();
          await page.mouse.move(0, 0);
          await reading.scrollIntoViewIfNeeded();
          await reading.focus();
          await reading.evaluate(async (node) => {
            window.__collectionReadingResult = node;
            await Promise.all(
              node
                .closest(".article-card")
                .getAnimations()
                .map((animation) => animation.finished.catch(() => {})),
            );
          });
          const before = await reading.boundingBox();
          deliveryGate = null;
          release();
          await page.locator(`${selector} .partner-placement`).waitFor();
          if (kind === "topic")
            await page.locator(".article-grid > .partner-placement").nth(1).waitFor();
          await assertCompactSidebar(page, selector, label, 1);
          assert.equal(
            await page.locator('.article-grid [data-ad-format="engagement"]').count(),
            0,
            `${label}: compact Engagement remains outside the article grid`,
          );
          const gridFormats = await page
            .locator(".article-grid > .partner-placement")
            .evaluateAll((cards) => cards.map((card) => card.dataset.adFormat));
          assert.deepEqual(gridFormats, kind === "topic" ? ["feed", "feed"] : []);
          assert.deepEqual(
            await page.locator(".collection-feed-articles .card-open-link").allTextContents(),
            feedArticles.map((item) => item.title),
            `${label}: delivery preserves all organic articles in order`,
          );
          assert.equal(
            await reading.evaluate(
              (node) =>
                node === window.__collectionReadingResult && node === document.activeElement,
            ),
            true,
            `${label}: asynchronous rail delivery keeps the focused article node`,
          );
          const after = await reading.boundingBox();
          assert.ok(
            Math.abs(after.y - before.y) <= 2,
            `${label}: rail delivery preserves reading position (${before.y} -> ${after.y})`,
          );
          const selection = queries.find(
            (query) => JSON.stringify(query.body.ad_formats) === JSON.stringify(["engagement"]),
          );
          assert.ok(selection, `${label}: compact sidebar makes its own contextual request`);
          assert.equal(selection.body.allow_sponsored_fallback ?? false, false);
          if (kind === "topic") {
            assert.equal(selection.body.surface, "topic");
            assert.equal(selection.body.topic_id, topic.id);
          } else {
            assert.equal(selection.body.surface, "feed");
            assert.deepEqual(
              selection.body.article_ids,
              feedArticles.map((item) => item.id),
            );
            assert.equal(queries.length, 1, `${label}: source has only one sidebar selection`);
          }
          const geometry = await page.locator(selector).evaluate((sidebar) => {
            const rect = (node) => node.getBoundingClientRect().toJSON();
            return {
              semantic: sidebar.tagName,
              sidebar: rect(sidebar),
              articles: rect(document.querySelector(".collection-feed-articles")),
              overflow: document.documentElement.scrollWidth > innerWidth,
            };
          });
          assert.equal(geometry.semantic, "ASIDE");
          if (width > 1100)
            assert.ok(
              geometry.sidebar.x >= geometry.articles.right,
              `${label}: desktop right rail`,
            );
          else
            assert.ok(
              geometry.sidebar.y >= geometry.articles.bottom - 1,
              `${label}: narrow layouts stack Engagement after articles`,
            );
          assert.equal(geometry.overflow, false);
          const products = await page
            .locator(".partner-placement")
            .evaluateAll((cards) => cards.map((card) => card.dataset.productId));
          assert.equal(new Set(products).size, products.length, `${label}: products stay unique`);
          await page.locator(selector).scrollIntoViewIfNeeded();
          await waitForVisibleProductLogos(page);
          await page.screenshot({
            path: `${reportDirectory}/${platform}-${kind}-sidebar-${width}.png`,
            animations: "disabled",
          });
        } finally {
          deliveryGate = null;
          release();
        }
      }
    }
    duplicateCollectionProduct = true;
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("about:blank");
    const selections = ["feed", "engagement"].map((format) =>
      page.waitForResponse((response) => {
        if (!new URL(response.url()).pathname.endsWith("/partner-placements/query")) return false;
        const query = response.request().postDataJSON();
        return query?.surface === "topic" && query.ad_formats?.includes(format);
      }),
    );
    await page.goto(`${base}/topics/typescript`);
    await Promise.all(selections.map(async (response) => (await response).json()));
    await page.locator(".article-grid .card-open-link").first().waitFor();
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
    assert.equal(
      await page.locator(`.partner-placement[data-product-id="${ordinary.product_id}"]`).count(),
      1,
      `${platform}: concurrent topic Feed and Engagement selections display a product only once`,
    );
    assert.equal(await page.locator('.article-grid [data-ad-format="engagement"]').count(), 0);
    await page.screenshot({
      path: `${reportDirectory}/${platform}-topic-sidebar-deduplication.png`,
      animations: "disabled",
    });
    duplicateCollectionProduct = false;
    organicCount = 3;
    queries = [];
    await page.goto("about:blank");
    await page.goto(`${base}/sources/engineering`);
    await page.locator(".article-grid .card-open-link").first().waitFor();
    await page.waitForTimeout(200);
    assert.equal(queries.length, 0, `${platform}: sparse source pages reserve no inventory`);
    assert.equal(await page.locator(selector).count(), 0);
    organicCount = 24;
    for (const route of ["/topics/typescript", "/sources/engineering"]) {
      queries = [];
      await page.goto("about:blank");
      await page.goto(`${base}${route}?cursor=next-page`);
      await page.locator(".article-grid .card-open-link").first().waitFor();
      await page.waitForTimeout(200);
      assert.equal(queries.length, 0, `${platform}: ${route} cursor pages reserve no inventory`);
      assert.equal(await page.locator(selector).count(), 0);
    }
  } finally {
    organicCount = 24;
    duplicateCollectionProduct = false;
    deliveryGate = null;
  }
}

async function assertCompactFeedTable(context, base, platform) {
  const originalSignedIn = signedIn;
  signedIn = false;
  try {
    for (const width of [1280, 390]) {
      for (const theme of ["light", "dark"]) {
        await context.clock.setSystemTime(receiptIssuedAt * 1000);
        const page = await context.newPage();
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        await page.addInitScript((theme) => {
          localStorage.setItem("devfeed:feed-view:guest", "compact");
          localStorage.setItem("devfeed:theme", theme);
        }, theme);
        const label = `${platform}/${width}/${theme}: compact Feed`;
        nativeMode = null;
        queries = [];
        const previousImpressions = [ordinary, sponsored].map((item) =>
          impressionCount(item.receipt),
        );
        try {
          await page.goto(`${base}/latest`);
          const rows = page.locator('.article-table tbody > [data-ad-format="feed"]');
          await rows.nth(1).waitFor();
          const structure = await page.locator(".article-table tbody").evaluate((body) =>
            [...body.children].map((row) => ({
              tag: row.tagName,
              cells: row.children.length,
              format: row.dataset.adFormat,
            })),
          );
          assert.equal(structure.length, 26, `${label}: two promotions retain all24article rows`);
          assert.ok(
            structure.every((row) => row.tag === "TR" && row.cells === 4),
            `${label}: valid four-column table structure`,
          );
          assert.equal(structure[4].format, "feed");
          assert.equal(structure[13].format, "feed");
          assert.equal(structure.filter((row) => row.format).length, 2);
          assert.equal(
            await page
              .locator(
                '.article-table [data-ad-format="engagement"],.article-table .card-image,.article-table .article-card,.article-grid',
              )
              .count(),
            0,
          );
          const organic = page.locator(
            ".article-table tbody > tr:not(.partner-placement) .article-list-title",
          );
          assert.deepEqual(
            await organic.allTextContents(),
            feedArticles.map((item) => item.title),
          );
          const selection = queries.find((query) => query.body.surface === "feed");
          assert.deepEqual(selection.body.ad_formats, ["feed"]);
          assert.deepEqual(
            selection.body.article_ids,
            feedArticles.map((item) => item.id),
          );
          for (const [index, item] of [ordinary, sponsored].entries()) {
            const row = rows.nth(index);
            assert.equal(
              (await row.locator(".source-column").textContent()).trim(),
              item.platform.name,
              `${label}: table attribution uses the selected launch platform`,
            );
            await row.scrollIntoViewIfNeeded();
            const badge = row.locator(".partner-placement-label:visible");
            assert.equal(
              await badge.count(),
              1,
              `${label}: exactly one sponsorship disclosure is visible`,
            );
            assert.equal(
              await badge.isVisible(),
              true,
              `${label}: Sponsored remains visible at every viewport`,
            );
            assert.equal(await badge.textContent(), "Sponsored");
            assert.equal(
              await row.locator("img,.card-image,button").count(),
              0,
              `${label}: table promotion has no hero or card controls`,
            );
            const geometry = await row.boundingBox();
            const rowLayout = await row.evaluate((node) => {
              const bounds = (element) => element.getBoundingClientRect().toJSON();
              const main = node.querySelector(".article-list-main");
              const title = main.querySelector("strong");
              const tagline = main.querySelector(".partner-placement-description");
              const badge = [...node.querySelectorAll(".partner-placement-label")].find(
                (element) => element.getBoundingClientRect().width > 0,
              );
              return {
                name: bounds(title),
                tagline: bounds(tagline),
                badgeCell: badge.closest("td").className,
                neighborHeight: node.previousElementSibling.getBoundingClientRect().height,
                icons: main.querySelectorAll("svg").length,
              };
            });
            const center = (rect) => rect.y + rect.height / 2;
            assert.ok(
              Math.abs(center(rowLayout.name) - center(rowLayout.tagline)) <= 3,
              `${label}: name and tagline share one line`,
            );
            assert.ok(
              rowLayout.tagline.x >= rowLayout.name.right,
              `${label}: tagline follows the name`,
            );
            assert.ok(
              geometry.height <= rowLayout.neighborHeight * 1.4,
              `${label}: promotion matches the table row density`,
            );
            assert.ok(
              rowLayout.badgeCell.includes(width >= 1280 ? "date-column" : "article-list-main"),
              `${label}: disclosure uses the date cell with a visible mobile fallback`,
            );
            assert.equal(rowLayout.icons, 0, `${label}: no extra outbound icon`);
            assert.equal(
              await row.locator(".partner-placement-description").textContent(),
              item.tagline,
            );
            await waitForImpression(page, item.receipt, previousImpressions[index]);
          }
          const clicksBefore = events.filter(
            (event) => event.kind === "click" && event.receipt === ordinary.receipt,
          ).length;
          const popupEvent = context.waitForEvent("page");
          await rows.first().locator(".partner-placement-link").click();
          const popup = await popupEvent;
          await popup.close();
          await page.waitForTimeout(100);
          assert.equal(
            events.filter((event) => event.kind === "click" && event.receipt === ordinary.receipt)
              .length,
            clicksBefore + 1,
            `${label}: signed row click is measured`,
          );
          assert.ok(
            await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
            `${label}: table has no page overflow`,
          );
          await page.screenshot({
            path: `${reportDirectory}/${platform}-feed-table-${theme}-${width}.png`,
          });
          const reading = organic.nth(8);
          await reading.scrollIntoViewIfNeeded();
          await reading.focus();
          await reading.evaluate((node) => {
            window.__tableReadingAnchor = node;
          });
          const before = await reading.boundingBox();
          const beforeRows = await rows.evaluateAll((nodes) => {
            window.__retainedFeedRows = nodes;
            return nodes.map((node) => node.getBoundingClientRect().height);
          });
          const beforeQueries = queries.length;
          const beforeEvents = events.length;
          await page.clock.fastForward(60 * 60_000 + 1000);
          await page.waitForFunction(() =>
            [...document.querySelectorAll(".article-table .partner-placement")].every(
              (row) => row.dataset.suspended === "true",
            ),
          );
          assert.equal(
            await page.locator('.article-table tbody > [data-ad-format="feed"]:visible').count(),
            0,
            `${label}: expired Feed rows stop displaying`,
          );
          const retained = await rows.evaluateAll((nodes) =>
            nodes.map((node, index) => ({
              same: node === window.__retainedFeedRows[index],
              inert: node.hasAttribute("inert"),
              height: node.getBoundingClientRect().height,
            })),
          );
          assert.equal(
            retained.length,
            beforeRows.length,
            `${label}: expired rows retain their table slots`,
          );
          retained.forEach((row, index) => {
            assert.equal(row.same, true, `${label}: expiry retains the original TR`);
            assert.equal(row.inert, true);
            assert.ok(Math.abs(row.height - beforeRows[index]) <= 1);
          });
          assert.equal(
            await reading.evaluate(
              (node) => node === window.__tableReadingAnchor && node === document.activeElement,
            ),
            true,
          );
          assert.ok(
            Math.abs((await reading.boundingBox()).y - before.y) <= 2,
            `${label}: expiry preserves reading position`,
          );
          await rows.locator(".partner-placement-link").evaluateAll((links) =>
            links.forEach((link) => {
              link.addEventListener("click", (event) => event.preventDefault(), { once: true });
              link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
            }),
          );
          await page.clock.runFor(2000);
          assert.equal(queries.length, beforeQueries, `${label}: expiry never refreshes inventory`);
          assert.equal(events.length, beforeEvents, `${label}: expired rows stop measurements`);
          await context.clock.setSystemTime(receiptIssuedAt * 1000);
          nativeMode = "table-header";
          queries = [];
          await page.goto(`${base}/`);
          await page.reload();
          await page.locator('.article-table tbody > [data-ad-format="feed"]').waitFor();
          if (width >= 1280)
            await page.locator('.topbar [data-ad-format="header_sponsorship"]').waitFor();
          assert.equal(
            await page.locator('.article-table [data-ad-format="header_sponsorship"]').count(),
            0,
          );
          assert.equal(
            await page.locator('.topbar [data-ad-format="header_sponsorship"]').count(),
            width >= 1280 ? 1 : 0,
            `${label}: Header credit stays in the global header`,
          );
          assert.ok(
            queries.every((query) => !query.body.ad_formats.includes("engagement")),
            `${label}: table never reserves Engagement inventory`,
          );
          await page.screenshot({
            path: `${reportDirectory}/${platform}-feed-table-header-${theme}-${width}.png`,
          });
        } finally {
          await page.evaluate(() => localStorage.removeItem("devfeed:feed-view:guest"));
          await page.close();
        }
      }
    }
  } finally {
    signedIn = originalSignedIn;
    nativeMode = null;
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
const headerSponsor = {
  ...ordinary,
  product_id: "12121212-1212-4212-8212-121212121212",
  asset_id: "34343434-3434-4434-8434-343434343434",
  name: "TypeScript platform",
  ad_format: "header_sponsorship",
  receipt: placementReceipt(
    "34343434-3434-4434-8434-343434343434",
    "56565656-5656-4656-8656-565656565656",
  ),
};
const sponsoredContent = {
  ...sponsored,
  ...customProductImage,
  ad_format: "sponsored_content",
  article: promotedArticle,
  url: `/articles/${promotedArticle.slug}`,
};
const engagementPlacement = { ...ordinary, ad_format: "engagement" };
const collectionEngagement = {
  ...engagementPlacement,
  product_id: "abababab-abab-4bab-8bab-abababababab",
  asset_id: "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
  name: "TypeScript developer tools",
  receipt: placementReceipt(
    "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
    "dededede-dede-4ede-8ede-dededededede",
  ),
};

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

function deliveryItems(items, query) {
  return items.map((item) => {
    const receipt =
      isolateArticleReceipts && query.surface === "article"
        ? placementReceipt(
            item.asset_id,
            createHash("sha256")
              .update(`article-${++articleReceiptSequence}`)
              .digest("hex")
              .slice(0, 32),
          )
        : item.receipt;
    acceptedTrackingReceipts.add(receipt);
    return {
      ...productMedia(item),
      platform:
        platformMode === "manual"
          ? null
          : platformMode === "missing"
            ? { ...item.platform, logo_url: null, logo_variants: [] }
            : platformMode === "broken"
              ? {
                  ...item.platform,
                  logo_url: "https://assets.partner.example/platforms/broken.png",
                  logo_variants: [],
                }
              : item.platform,
      receipt,
    };
  });
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
        text: footer.firstElementChild.textContent.trim(),
        action: footer.querySelector(".partner-placement-action")?.textContent.trim() ?? null,
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
    `${label}: destination identifies the product domain`,
  );
  assert.equal(treatment.destination.icons, 0, `${label}: the destination has no extra arrow`);
  assert.equal(
    treatment.destination.action,
    null,
    `${label}: the destination has no extra action text`,
  );
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
    if (body.surface === "header") {
      headerQueries.push({ pathname, body });
      assert.deepEqual(body.ad_formats, ["header_sponsorship"]);
      const { exclude_product_ids, ...selection } = body;
      assert.deepEqual(selection, { surface: "header", ad_formats: ["header_sponsorship"] });
      assert.ok(Array.isArray(exclude_product_ids));
      return {
        items: deliveryItems(
          headerInventory || ["header-gallery", "table-header"].includes(nativeMode)
            ? [headerSponsor]
            : [],
          body,
        ),
      };
    }
    queries.push({ pathname, body });
    assert.ok(
      !body.ad_formats?.includes("header_sponsorship"),
      "Contextual placements never reserve global header inventory",
    );
    if (
      ["feed", "topic"].includes(body.surface) &&
      body.ad_formats?.some((format) => format !== "engagement")
    )
      assert.ok(
        !body.ad_formats?.includes("engagement"),
        "Grid and table feeds request native Feed cards without sidebar Engagement",
      );
    if (nativeMode) {
      assert.ok(Array.isArray(body.ad_formats), "Native formats are explicitly requested");
      if (body.surface !== "feed") assert.ok(!body.ad_formats.includes("sponsored_content"));
      const items =
        nativeMode === "disclosure"
          ? []
          : nativeMode === "table-header"
            ? [sponsored]
            : nativeMode === "sponsored-content"
              ? [sponsoredContent]
              : nativeMode === "header-gallery" && body.allow_sponsored_fallback
                ? [sponsoredContent]
                : [engagementPlacement, sponsored];
      return {
        items: deliveryItems(
          nativeMode === "unexpected-engagement"
            ? [engagementPlacement]
            : items.filter(
                (item) =>
                  body.ad_formats.includes(item.ad_format ?? "feed") &&
                  !body.exclude_product_ids?.includes(item.product_id),
              ),
          body,
        ),
      };
    }
    if (body.surface === "article")
      assert.deepEqual(
        body.ad_formats,
        ["engagement"],
        "Article sidebars request compact Engagement only",
      );
    if (body.surface === "search") {
      assert.deepEqual(
        body.ad_formats,
        ["engagement"],
        "Search collections request Engagement only",
      );
      assert.notEqual(
        body.allow_sponsored_fallback,
        true,
        "Search collections never permit generic sponsorship",
      );
      assert.ok(body.article_ids.length >= 4 && body.article_ids.length <= 24);
    }
    if (
      ["topic", "feed"].includes(body.surface) &&
      JSON.stringify(body.ad_formats) === JSON.stringify(["engagement"])
    )
      return {
        items: deliveryItems(
          body.surface === "topic" || body.article_ids?.includes(article.id)
            ? [duplicateCollectionProduct ? engagementPlacement : collectionEngagement]
            : [],
          body,
        ),
      };
    const eligible =
      body.surface === "topic" ||
      (pathname.includes("/user/") && body.surface === "feed") ||
      (["feed", "search"].includes(body.surface) && body.article_ids?.includes(article.id)) ||
      (body.surface === "article" && feedArticles.some((item) => item.id === body.article_id));
    return {
      items: deliveryItems(
        eligible
          ? (body.ad_formats?.length === 1 && body.ad_formats[0] === "engagement"
              ? body.surface === "article"
                ? [engagementPlacement, { ...sponsored, ad_format: "engagement" }]
                : [engagementPlacement]
              : [ordinary, sponsored]
            ).filter((item) => !body.exclude_product_ids?.includes(item.product_id))
          : [],
        body,
      ),
    };
  }
  if (pathname === "/v1/partner-tracking/events") {
    assert.equal(headers.cookie, undefined);
    assert.equal(headers.authorization, undefined);
    assert.deepEqual(Object.keys(body).sort(), ["kind", "receipt"]);
    assert.ok(acceptedTrackingReceipts.has(body.receipt));
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
      visibility: {
        public: true,
        location: false,
        stack: false,
        heatmap: false,
        achievements: false,
      },
    };
  }
  if (pathname === "/v1/user/profiles/fixture-reader")
    return {
      display_name: "Reader",
      username: "fixture-reader",
      avatar_url: null,
      links: [],
      stack: [],
      visibility: {
        public: true,
        location: false,
        stack: false,
        heatmap: false,
        achievements: false,
      },
    };
  if (pathname === "/v1/user/profiles/fixture-reader/reading-heatmap")
    return { year: 2026, timezone: "UTC", days: [] };
  if (pathname === "/v1/users/fixture-reader")
    return {
      profile: response("/v1/user/profiles/fixture-reader"),
      activity: null,
    };
  if (pathname === "/v1/user/bookmarks") return { items: feedArticles, next_cursor: null };
  if (pathname === "/v1/user/settings/appearance")
    return { theme: "system", timezone: "UTC", date_format: "locale", time_format: "24" };
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
  if ([`/v1/sources/${source.slug}`, `/v1/sources/${source.id}`].includes(pathname)) return source;
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
    if (["feed", "search", "topic", "header"].includes(body?.surface) && deliveryGate)
      await deliveryGate;
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
    headerQueries = [];
    headerInventory = false;
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
          if (["feed", "search", "topic", "header"].includes(body?.surface) && deliveryGate)
            await deliveryGate;
          const json = response(request.url(), body, request.headers(), true);
          await route.fulfill(
            request.url().endsWith("/partner-tracking/events") ? { status: 204 } : { json },
          );
        });
      }
      await context.route("https://assets.partner.example/**", async (route) => {
        if (route.request().url().includes("/platforms/broken.png") && platformLogoGate)
          await platformLogoGate;
        return route.request().url().endsWith("broken.png")
          ? route.fulfill({ status: 404, body: "Missing" })
          : route.fulfill({
              contentType: "image/svg+xml",
              body: route.request().url().includes("/platforms/")
                ? '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#7c3aed"/><text x="11" y="44" fill="white" font-size="32">LP</text></svg>'
                : route.request().url().includes("/ads/")
                  ? '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><rect width="1200" height="675" fill="#173b35"/><text x="100" y="280" fill="#b7f3c8" font-size="64" font-family="sans-serif">A custom product creative</text><text x="100" y="390" fill="white" font-size="42" font-family="sans-serif">Built for native placements.</text></svg>'
                  : route.request().url().includes("/og/") ||
                      route.request().url().includes("/articles/")
                    ? '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><defs><linearGradient id="bg"><stop stop-color="#122b5c"/><stop offset="1" stop-color="#3178c6"/></linearGradient></defs><rect width="1200" height="675" fill="url(#bg)"/><text x="100" y="270" fill="white" font-size="68" font-family="sans-serif">TypeScript Validator</text><text x="100" y="390" fill="#a5d8ff" font-size="42" font-family="sans-serif">Safe inputs. Confident applications.</text></svg>'
                    : '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" rx="10" fill="#3178c6"/><text x="8" y="33" fill="white" font-size="24">TS</text></svg>',
            });
      });
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
      await assertSitewideHeader(context, base, platform);
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
      await assertCompactSidebar(page, ".preview-sidebar", `${platform}: desktop article`, 2);
      assert.ok(
        queries
          .filter((query) => query.body.surface === "article")
          .every(
            (query) => JSON.stringify(query.body.ad_formats) === JSON.stringify(["engagement"]),
          ),
        `${platform}: article previews request Engagement inventory only`,
      );
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
          await page.locator(".article-grid .partner-placements").count(),
          0,
          `${platform}: ${label} grid has no separate recommendation banner`,
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
        assert.deepEqual(
          await page
            .locator(".article-grid > .partner-placement")
            .evaluateAll((cards) => cards.map((card) => card.dataset.adFormat)),
          ["feed", "feed"],
          `${platform}: ${label} uses full Feed cards in the article grid`,
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
      await assertCompactSidebar(
        page,
        ".article-modal .preview-sidebar",
        `${platform}: modal article`,
        2,
      );
      await assertSponsoredPill(
        page,
        ".article-modal .preview-sidebar .partner-placement-label",
        `${platform}: modal preview`,
        ".article-grid > .article-card .card-meta-type",
      );
      assert.equal(
        await page.locator(".article-grid > .partner-placement:visible").count(),
        2,
        `${platform}: covered feed products stay painted behind the article preview`,
      );
      assert.equal(
        await page.locator('.article-grid > .partner-placement[data-suspended="true"]').count(),
        0,
        `${platform}: opening a preview does not suspend the product artwork`,
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
        `${platform}: covered background cards keep their artwork and leave the interaction/accessibility tree`,
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
      await assertCompactSidebar(page, ".preview-sidebar", `${platform}: mobile article`, 2);
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
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        for (const filtered of [false, true]) {
          queries = [];
          let release;
          deliveryGate = new Promise((resolve) => {
            release = resolve;
          });
          const route = filtered
            ? "/search?q=typescript&section=articles&content_types=tutorial&sort=newest&date_from=2026-10-01&date_to=2026-10-13"
            : "/search?q=typescript";
          await page.goto("about:blank");
          await page.goto(`${base}${route}`);
          const readingResult = page.locator(".search-section-articles .search-result-link").nth(8);
          await readingResult.waitFor();
          await readingResult.scrollIntoViewIfNeeded();
          await readingResult.focus();
          await readingResult.evaluate((node) => {
            window.__searchReadingResult = node;
          });
          await page.evaluate(
            () =>
              new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
          );
          const before = await readingResult.boundingBox();
          deliveryGate = null;
          release();
          const engagement = page.locator('.search-sidebar [data-ad-format="engagement"]');
          await engagement.waitFor();
          const label = `${platform}: ${width}px ${filtered ? "filtered articles" : "all results"} search`;
          await assertCompactSidebar(page, ".search-sidebar", label, 1);
          assert.equal(
            await page.locator(".search-section-articles .partner-placement").count(),
            0,
          );
          assert.deepEqual(
            await page.locator(".search-section-articles .search-result-link").allTextContents(),
            feedArticles.map((item) => item.title),
            `${label}: sidebar delivery retains every organic result in order`,
          );
          assert.equal(
            await readingResult.evaluate(
              (node) => node === window.__searchReadingResult && node === document.activeElement,
            ),
            true,
            `${label}: asynchronous delivery preserves the original focused result`,
          );
          const after = await readingResult.boundingBox();
          assert.ok(
            Math.abs(after.y - before.y) <= 2,
            `${label}: delivery preserves reading position`,
          );
          const searchQuery = queries.find((query) => query.body.surface === "search");
          assert.equal(searchQuery.pathname, "/v1/partner-placements/query");
          assert.deepEqual(searchQuery.body.ad_formats, ["engagement"]);
          assert.equal(
            searchQuery.body.allow_sponsored_fallback ?? false,
            false,
            `${label}: stays contextual`,
          );
          assert.deepEqual(
            searchQuery.body.article_ids,
            feedArticles.map((item) => item.id),
          );
          assert.equal(
            await page
              .locator(
                '[data-ad-format="header_sponsorship"],[data-ad-format="feed"],[data-ad-format="sponsored_content"]',
              )
              .count(),
            0,
          );
          assert.equal(await page.getByText(ordinary.reason, { exact: true }).count(), 0);
          const geometry = await page.locator(".search-sidebar").evaluate((sidebar) => {
            const rect = (node) => node?.getBoundingClientRect().toJSON();
            const articleResults = document.querySelector(".search-section-articles");
            const topics = sidebar.querySelector(".search-section-topics");
            const placement = sidebar.querySelector(".partner-placement");
            return {
              semantic: sidebar.tagName,
              sidebar: rect(sidebar),
              articles: rect(articleResults),
              placement: rect(placement),
              topics: rect(topics),
              beforeTopics:
                !topics ||
                Boolean(
                  placement.compareDocumentPosition(topics) & Node.DOCUMENT_POSITION_FOLLOWING,
                ),
            };
          });
          assert.equal(geometry.semantic, "ASIDE");
          assert.equal(geometry.beforeTopics, true, `${label}: compact sponsor precedes Topics`);
          if (filtered)
            assert.equal(
              geometry.topics,
              undefined,
              `${label}: article filter does not add catalog sections`,
            );
          else
            assert.ok(
              geometry.placement.bottom <= geometry.topics.y,
              `${label}: card sits above Topics`,
            );
          if (width >= 1024)
            assert.ok(
              geometry.sidebar.x >= geometry.articles.right,
              `${label}: compact card belongs in the right rail`,
            );
          else
            assert.ok(
              geometry.sidebar.y >= geometry.articles.bottom - 1,
              `${label}: mobile stacks the rail after article results`,
            );
          await engagement.scrollIntoViewIfNeeded();
          await page.mouse.move(5, 5);
          await waitForVisibleProductLogos(page);
          assert.equal(await engagement.locator("img").getAttribute("src"), ordinary.logo_url);
          assert.ok(
            await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
            `${label}: no horizontal overflow`,
          );
          await page.screenshot({
            path: `${reportDirectory}/${platform}-actual-search-${filtered ? "filtered-" : ""}${width === 390 ? "mobile" : "desktop"}.png`,
          });
        }
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
      await assertCollectionFeedSidebars(page, base, platform);
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
            await assertPlacementPlatform(card, {
              label: `${platform}/${theme}/${width}/${mode}: managed platform logo`,
            });
            const geometry = await card.evaluate((product) => {
              const top = product.getBoundingClientRect().top;
              const neighbor =
                [...product.parentElement.children].find(
                  (node) =>
                    node.classList.contains("article-card") &&
                    !node.classList.contains("partner-placement") &&
                    Math.abs(node.getBoundingClientRect().top - top) <= 1,
                ) ?? product.previousElementSibling;
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
      await assertPlatformFallbacks(page, base, platform);
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
          ["/search?q=typescript", "search"],
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
          if (surface === "feed" || surface === "search")
            assert.ok(queries.every((query) => !query.body.allow_sponsored_fallback));
          if (surface === "search")
            assert.ok(
              queries.every(
                (query) => JSON.stringify(query.body.ad_formats) === JSON.stringify(["engagement"]),
              ),
            );
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
        await page.goto("about:blank");
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        mediaMode = "custom";
        nativeMode = "header-gallery";
        queries = [];
        headerQueries = [];
        events = [];
        let releaseNative;
        deliveryGate = new Promise((resolve) => {
          releaseNative = resolve;
        });
        await page.goto(`${base}/`);
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
        const headerBefore = await page.locator(".topbar").evaluate((header) => ({
          height: header.getBoundingClientRect().height,
          searchWidth: header.querySelector(".search").getBoundingClientRect().width,
          actionsWidth: header.querySelector(".header-actions").getBoundingClientRect().width,
        }));
        deliveryGate = null;
        releaseNative();
        const credit = page.locator(".topbar .partner-placement--header_sponsorship");
        const promoted = page.locator(
          '.article-grid > .article-card[data-ad-format="sponsored_content"]',
        );
        await promoted.waitFor();
        if (width >= 1280) await credit.waitFor();
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
          `${platform}: header credit and sponsored content keep reading position (${readingBefore.y} -> ${readingAfter.y})`,
        );
        assert.equal(
          await page.locator(".partner-placement:visible").count(),
          width >= 1280 ? 2 : 1,
          `${platform}: native inventory fits the available header space`,
        );
        assert.equal(
          await credit.locator(".card-image,.partner-placement-destination,button").count(),
          0,
        );
        assert.equal(
          await page
            .locator(
              '.article-placement-feed > [data-ad-format="header_sponsorship"], .article-grid > [data-ad-format="header_sponsorship"]',
            )
            .count(),
          0,
          `${platform}: header sponsorship never adds a banner or feed card`,
        );
        assert.deepEqual(
          queries.find((query) => query.body.surface === "feed").body.ad_formats,
          ["feed", "sponsored_content"],
          `${platform}: delivery only requests formats that can be displayed`,
        );
        if (width >= 1280) {
          assert.equal(headerQueries.length, 1, `${platform}: one independent Header query`);
          await credit.scrollIntoViewIfNeeded();
          await waitForVisibleProductLogos(page);
          assert.equal(await credit.locator("img").getAttribute("src"), ordinary.logo_url);
          assert.equal(await credit.locator(".partner-placement-label").textContent(), "Sponsored");
          const tagline = credit.locator(".partner-placement-description");
          assert.equal(await tagline.textContent(), ordinary.tagline);
          assert.equal(
            await tagline.isVisible(),
            true,
            `${platform}: header credit explains the product`,
          );
          const headerLayout = await credit.evaluate((node) => {
            const bounds = (element) => element.getBoundingClientRect().toJSON();
            const header = node.closest(".topbar");
            return {
              credit: bounds(node),
              header: bounds(header),
              logo: bounds(node.querySelector(".partner-placement-logo")),
              peers: [...header.children]
                .filter((element) => !element.contains(node))
                .map(bounds)
                .filter((rect) => rect.width && rect.height),
              pageWidth: document.documentElement.scrollWidth,
            };
          });
          assert.ok(headerLayout.credit.height <= 48, `${platform}: header credit remains compact`);
          assert.equal(
            headerLayout.logo.width,
            32,
            `${platform}: credit uses a recognizable brand mark`,
          );
          assert.ok(headerLayout.pageWidth <= width, `${platform}: header fits the viewport`);
          assert.equal(
            headerLayout.header.height,
            headerBefore.height,
            `${platform}: sponsorship never adds another header row`,
          );
          assert.ok(
            headerLayout.credit.x >= headerLayout.header.x &&
              headerLayout.credit.right <= headerLayout.header.right + 1 &&
              headerLayout.credit.y >= headerLayout.header.y &&
              headerLayout.credit.bottom <= headerLayout.header.bottom + 1,
            `${platform}: sponsorship stays inside the global header`,
          );
          for (const peer of headerLayout.peers)
            assert.ok(
              headerLayout.credit.right <= peer.x + 1 ||
                headerLayout.credit.x >= peer.right - 1 ||
                headerLayout.credit.bottom <= peer.y + 1 ||
                headerLayout.credit.y >= peer.bottom - 1,
              `${platform}: sponsorship never overlaps the brand, search or account controls`,
            );
          assert.ok(
            headerLayout.credit.width < headerLayout.header.width / 2,
            `${platform}: sponsorship uses spare header space without becoming a banner`,
          );
        } else {
          assert.equal(
            await page.locator('[data-ad-format="header_sponsorship"]').count(),
            0,
            `${platform}: narrow headers have no header sponsorship`,
          );
          assert.ok(
            headerQueries.length === 0,
            `${platform}: narrow screens never reserve unavailable Header inventory`,
          );
          const headerAfter = await page.locator(".topbar").evaluate((header) => ({
            height: header.getBoundingClientRect().height,
            searchWidth: header.querySelector(".search").getBoundingClientRect().width,
            actionsWidth: header.querySelector(".header-actions").getBoundingClientRect().width,
          }));
          assert.deepEqual(
            headerAfter,
            headerBefore,
            `${platform}: native delivery never squeezes narrow navigation controls`,
          );
        }
        await page.screenshot({
          path: `${reportDirectory}/${platform}-header-sponsorship-${width === 390 ? "mobile" : "desktop"}.png`,
          animations: "disabled",
        });
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
              '.preview-sidebar [data-ad-format="header_sponsorship"],.preview-sidebar [data-ad-format="sponsored_content"]',
            )
            .count(),
          0,
        );
        nativeMode = "unexpected-engagement";
        queries = [];
        await page.goto("about:blank");
        const unexpectedSelection = page.waitForResponse((response) => {
          if (!new URL(response.url()).pathname.endsWith("/partner-placements/query")) return false;
          return response.request().postDataJSON()?.surface === "feed";
        });
        await page.goto(`${base}/?q=typescript`);
        await page.locator(".article-grid .card-open-link").first().waitFor();
        await unexpectedSelection;
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        assert.equal(
          await page.locator(".partner-placement").count(),
          0,
          `${platform}/${width}: unrequested Engagement inventory never renders in a feed grid`,
        );
        assert.deepEqual(
          await page.locator(".article-grid .card-open-link").allTextContents(),
          feedArticles.map((item) => item.title),
          `${platform}/${width}: rejecting sidebar inventory preserves all organic grid cards`,
        );
        assert.ok(
          queries.length &&
            queries.every(
              (query) =>
                query.body.surface === "feed" &&
                !query.body.allow_sponsored_fallback &&
                JSON.stringify(query.body.ad_formats) ===
                  JSON.stringify(["feed", "sponsored_content"]),
            ),
          `${platform}/${width}: filtered grids request Feed and sponsored articles only`,
        );
        await page.screenshot({
          path: `${reportDirectory}/${platform}-grid-rejects-engagement-${width === 390 ? "mobile" : "desktop"}.png`,
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
      await assertFeedPlacementContinuity(page, base, platform);
      await assertMountedPlacementExpiry(context, base, platform);
      for (const pathname of ["/v1/partner-placements/query", "/v1/partner-tracking/events"])
        assert.ok(
          observedCallerProofs.get(pathname)?.has(callerFixtureIps[platform]),
          `${platform}: production gateway preserves distinct caller identity for ${pathname}`,
        );
      console.log(
        `${platform}: built web/extension sitewide Header sponsorship, stable header DOM and signed receipts through page navigation and article overlays, independent Header expiry, narrow inventory gating, compact search and article sidebars, category and topic cards; managed custom product creatives, clearing to default OG, responsive sources and failed-image fallback; compact formats preserve logos and sponsored content preserves its article image; every product disclosed as Sponsored with quiet header text and amber card pills; no dismissal; compact preview sidebar; unchanged feed nodes, receipts and tracking through article overlays; focus/layout stability; signed campaign and non-campaign tracking; configured production gateway caller proofs, credential isolation and placement429 retry guidance; mounted receipt expiry and background wake preserve reading position without refreshing or tracking; viewable placements never read, write or remove abandoned local history; private session routing; optional private experience save/reload/clear; article priority passed`,
      );
    } catch (error) {
      const page = context.pages().findLast((page) => page.url() !== "about:blank");
      console.error("Placement browser failure", platform, {
        signedIn,
        nativeMode,
        queries,
        headerQueries,
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
