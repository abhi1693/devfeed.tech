import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { openArticleViewSettings, setArticleView } from "./article-view.mjs";

export const stackedReaderTitle =
  "Engineering boundaries — 日本語, العربية, café, 👩🏽‍💻: " +
  "a dependable approach to application architecture and international collaboration "
    .repeat(3)
    .trim();
export const stackedReaderDescription =
  "A practical overview with Unicode 日本語 and an unusually long identifier " +
  "uninterrupted".repeat(35) +
  ". " +
  "Learn how teams build useful tools without losing the details that matter. ".repeat(9).trim();

export function stackedReaderArticle(article, index = 0) {
  return {
    ...article,
    id: index ? `33333333-3333-4333-8333-${String(index).padStart(12, "0")}` : article.id,
    slug: index ? `stacked-reader-${index}` : article.slug,
    title: index === 0 ? stackedReaderTitle : `Stacked article ${index + 1}`,
    ai_description: index === 0 ? stackedReaderDescription : null,
    summary: index === 1 ? "" : "A concise source excerpt about dependable engineering.",
    ai_summary: index === 1 ? null : "A useful AI overview of engineering boundaries.",
    image_url: index === 1 ? null : article.image_url,
    image_variants: index === 1 ? [] : article.image_variants,
    sources: index === 1 ? [] : article.sources,
    topics: index === 1 ? [] : article.topics,
    tags: index === 1 ? [] : article.tags,
    author: index === 1 ? null : article.author,
    published_at: index === 1 ? null : "2020-02-29T23:59:59Z",
    feed_at: "2026-10-21T00:00:00Z",
    canonical_url:
      index === 1 ? "https://fallback-publisher.example.test/article" : article.canonical_url,
  };
}

export function stackedReaderFeed(article, params) {
  const next = params.has("cursor");
  return {
    items: next
      ? [stackedReaderArticle(article, 12)]
      : Array.from({ length: 12 }, (_, index) => stackedReaderArticle(article, index)),
    next_cursor: next ? null : "stacked-next-page",
  };
}

async function mockStackedPlacements(page) {
  const context = page.context();
  const events = [];
  const image = "https://stacked-sponsor.example.test/launchpad.svg";
  const logo = "https://stacked-sponsor.example.test/launchpad-logo.svg";
  const receipt = (asset, delivery) =>
    `v1.${asset.replaceAll("-", "")}.${delivery.replaceAll("-", "")}.${Math.floor(Date.now() / 1000)}.${"a".repeat(64)}`;
  const product = {
    product_id: "11111111-aaaa-4111-8111-111111111111",
    asset_id: "22222222-aaaa-4222-8222-222222222222",
    campaign_id: "33333333-aaaa-4333-8333-333333333333",
    name: "LaunchPad for developers",
    tagline: "Launch useful tools, share releases, and find your next developer product.",
    url: "https://stacked-sponsor.example.test/product",
    logo_url: logo,
    image_url: image,
    image_variants: [],
    link_rel: ["nofollow"],
    partner_name: "Stack Sponsor",
    platform: { name: "Developer LaunchPad", logo_url: null, logo_variants: [] },
    attribution: "Brought to you by Stack Sponsor",
    sponsored: true,
    reason: "A developer product relevant to the engineering feed.",
    receipt: receipt(
      "22222222-aaaa-4222-8222-222222222222",
      "44444444-aaaa-4444-8444-444444444444",
    ),
    ad_format: "feed",
    article: null,
  };
  const promoted = {
    ...product,
    product_id: "55555555-aaaa-4555-8555-555555555555",
    asset_id: "66666666-aaaa-4666-8666-666666666666",
    name: "An engineering guide from Stack Sponsor",
    url: "/articles/stacked-sponsored-engineering",
    receipt: receipt(
      "66666666-aaaa-4666-8666-666666666666",
      "77777777-aaaa-4777-8777-777777777777",
    ),
    ad_format: "sponsored_content",
    article: {
      id: "88888888-aaaa-4888-8888-888888888888",
      slug: "stacked-sponsored-engineering",
      canonical_url: "https://stacked-sponsor.example.test/engineering-guide",
      title: "An engineering guide from Stack Sponsor",
      summary: "Practical advice for building dependable developer tools and useful releases.",
      ai_summary: null,
      ai_description: null,
      image_url: image,
      image_variants: [],
      author: null,
      content_type: "tutorial",
      content_format: "article",
      language: "en",
      published_at: "2026-10-21T00:00:00Z",
      feed_at: "2026-10-21T00:00:00Z",
      tags: [],
      sources: [],
      topics: [],
      sponsor_names: ["Stack Sponsor"],
    },
  };
  const queryPattern = /\/api\/v1\/(?:user\/)?partner-placements\/query$/;
  const queryHandler = (route) => {
    const query = route.request().postDataJSON();
    return route.fulfill({
      json: {
        items:
          query.surface === "feed"
            ? [product, promoted].filter((item) => query.ad_formats?.includes(item.ad_format))
            : [],
      },
    });
  };
  const trackingPattern = /\/api\/v1\/partner-tracking\/events$/;
  const trackingHandler = (route) => {
    events.push(route.request().postDataJSON());
    return route.fulfill({ status: 204 });
  };
  const imagePattern = "https://stacked-sponsor.example.test/*.svg";
  const imageHandler = (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body:
        route.request().url() === logo
          ? `<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128"><rect width="128" height="128" rx="24" fill="#00786f"/><path d="M35 30V94H65V82H49V30ZM72 30V94H86V70H94C118 70 118 30 94 30ZM86 43H93C101 43 101 57 93 57H86Z" fill="white"/></svg>`
          : `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450"><rect width="800" height="450" fill="#f8fbfc"/><path d="M0 0H800V450H0Z" fill="none" stroke="#009d8f" stroke-width="16"/><text x="400" y="190" text-anchor="middle" fill="#00786f" font-family="sans-serif" font-size="72" font-weight="bold">LaunchPad</text><text x="400" y="270" text-anchor="middle" fill="#173c3a" font-family="sans-serif" font-size="40">Developer tools, all in one place</text></svg>`,
    });
  await context.route(queryPattern, queryHandler);
  await context.route(trackingPattern, trackingHandler);
  await context.route(imagePattern, imageHandler);
  return {
    events,
    product,
    promoted,
    cleanup: async () => {
      await context.unroute(queryPattern, queryHandler);
      await context.unroute(trackingPattern, trackingHandler);
      await context.unroute(imagePattern, imageHandler);
    },
  };
}

async function checkStackedSponsorship(page, output) {
  const placements = page.locator(".article-stacked > .partner-placement:visible");
  await placements.first().waitFor();
  await page.waitForFunction(
    () => document.querySelectorAll(".article-stacked > .partner-placement").length === 2,
  );
  const measurements = [];
  for (const width of [1800, 1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const format of ["feed", "sponsored_content"]) {
      const placement = placements.and(page.locator(`[data-ad-format="${format}"]`));
      await placement.evaluate((node) =>
        node.scrollIntoView({ block: "center", behavior: "instant" }),
      );
      await placement.locator("img").first().waitFor();
      await placement
        .locator("img")
        .first()
        .evaluate(async (node) => {
          if (!node.complete)
            await new Promise((resolve) => node.addEventListener("load", resolve, { once: true }));
        });
      const snapshot = await placement.evaluate((node) => {
        const box = (element) => element?.getBoundingClientRect().toJSON();
        const thumbnail = node.querySelector(".article-stacked-thumbnail");
        const brand = node.querySelector(".stacked-placement-brand .partner-placement-logo");
        const displayedImage = thumbnail ?? brand;
        const image = displayedImage?.querySelector("img");
        const previous = node.previousElementSibling;
        const badge = node.querySelector('[data-promotion-kind="sponsored"]');
        return {
          format: node.dataset.adFormat,
          viewport: innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          placement: box(node),
          card: box(node.querySelector(":scope > .partner-placement-link")),
          cardBackground: node.querySelector(":scope > .partner-placement-link")
            ? getComputedStyle(node.querySelector(":scope > .partner-placement-link"))
                .backgroundColor
            : null,
          thumbnail: box(thumbnail),
          brand: box(brand),
          visit: box(node.querySelector(".stacked-placement-visit")),
          image: box(image),
          imageFit: image ? getComputedStyle(image).objectFit : null,
          copy: box(node.querySelector(".stacked-placement-copy, .article-stacked-copy")),
          previousThumbnail: box(previous?.querySelector(".article-stacked-thumbnail")),
          previousCopy: box(previous?.querySelector(".article-stacked-copy")),
          titleFont: getComputedStyle(node.querySelector("h2")).fontSize,
          previousTitleFont: getComputedStyle(previous.querySelector("h2")).fontSize,
          metadataFont: getComputedStyle(
            node.querySelector(".stacked-placement-meta, .article-stacked-meta"),
          ).fontSize,
          previousMetadataFont: getComputedStyle(previous.querySelector(".article-stacked-meta"))
            .fontSize,
          background: getComputedStyle(node).backgroundColor,
          previousBackground: getComputedStyle(previous).backgroundColor,
          rail: getComputedStyle(node).boxShadow,
          badgeBackground: getComputedStyle(badge).backgroundColor,
          text: node.textContent,
        };
      });
      assert.ok(snapshot.documentWidth <= width + 1, `${width}px sponsorship has no page overflow`);
      assert.ok(
        snapshot.placement.x >= -1 && snapshot.placement.x + snapshot.placement.width <= width + 1,
        `${width}px ${format} stays inside the Stacked stream`,
      );
      assert.equal(snapshot.metadataFont, snapshot.previousMetadataFont);
      const disclosure = placement.locator('[data-promotion-kind="sponsored"]');
      assert.equal(await disclosure.isVisible(), true, "Sponsorship remains clearly disclosed");
      assert.match(await disclosure.textContent(), /^Sponsored/);
      assert.equal(snapshot.rail, "none", "Sponsorship uses its disclosure without an extra rail");
      assert.notEqual(snapshot.badgeBackground, "rgba(0, 0, 0, 0)");
      if (format === "feed") {
        assert.equal(
          snapshot.thumbnail,
          undefined,
          "Product promotions use a logo instead of a large creative thumbnail",
        );
        assert.ok(Math.abs(snapshot.brand.width - (width > 520 ? 56 : 48)) <= 1);
        assert.ok(Math.abs(snapshot.brand.height - snapshot.brand.width) <= 1);
        assert.equal(
          snapshot.background,
          "rgba(0, 0, 0, 0)",
          "The full-width slot has a neutral background",
        );
        assert.ok(snapshot.card.width <= 761, "The product and action stay in a bounded card");
        assert.ok(
          Math.abs(
            snapshot.card.x +
              snapshot.card.width / 2 -
              (snapshot.placement.x + snapshot.placement.width / 2),
          ) <= 1,
          "The promotion is centered within the Stacked stream",
        );
        assert.ok(
          snapshot.visit.x >= snapshot.card.x &&
            snapshot.visit.x + snapshot.visit.width <= snapshot.card.x + snapshot.card.width,
          "The call to action belongs to the bounded card",
        );
        assert.ok(parseFloat(snapshot.titleFont) > parseFloat(snapshot.metadataFont));
        assert.equal(
          await placement.locator(".card-image, .stacked-placement-thumbnail").count(),
          0,
        );
        assert.equal(
          snapshot.text.split("Brought to you by Stack Sponsor").length - 1,
          1,
          "Attribution appears once alongside the Sponsored disclosure",
        );
        assert.equal(await placement.locator(".stacked-placement-footer").count(), 0);
        assert.equal(await placement.getByText("Visit site", { exact: true }).count(), 1);
        assert.ok(
          snapshot.visit.height >= 44,
          "The destination has a clear touch-sized call to action",
        );
        if (width > 520) {
          assert.ok(snapshot.visit.x >= snapshot.copy.x + snapshot.copy.width - 1);
          assert.ok(
            snapshot.card.height <= 140,
            "Desktop sponsorship keeps its content compact within the card",
          );
          assert.ok(
            snapshot.visit.x + snapshot.visit.width < width - 64,
            "The destination never drifts to the screen edge",
          );
        } else {
          assert.ok(Math.abs(snapshot.visit.x - snapshot.copy.x) <= 1);
          assert.ok(snapshot.visit.y >= snapshot.copy.y + snapshot.copy.height - 1);
        }
        assert.ok(
          snapshot.card.height <=
            Math.max(snapshot.brand.height, snapshot.copy.height) + (width > 520 ? 48 : 98),
          "The bounded product card follows its content",
        );
        await placement
          .getByRole("link", { name: "LaunchPad for developers", exact: true })
          .hover();
        assert.equal(
          await placement
            .locator(".partner-placement-link")
            .evaluate((node) => getComputedStyle(node).backgroundColor),
          snapshot.cardBackground,
          "Hover keeps the card surface neutral",
        );
        const contrast = await checkStackedBadgeContrast(disclosure);
        snapshot.contrast = contrast;
      } else {
        assert.equal(snapshot.titleFont, snapshot.previousTitleFont);
        assert.ok(
          Math.abs(snapshot.thumbnail.width - snapshot.previousThumbnail.width) <= 1 &&
            Math.abs(snapshot.thumbnail.height - snapshot.previousThumbnail.height) <= 1,
          `${width}px sponsored articles use the same thumbnail as their organic neighbor`,
        );
        assert.ok(Math.abs(snapshot.thumbnail.width / snapshot.thumbnail.height - 16 / 9) <= 0.02);
        assert.equal(
          snapshot.imageFit,
          "contain",
          "Sponsored article artwork remains fully visible",
        );
        assert.ok(
          Math.abs(snapshot.thumbnail.x - snapshot.previousThumbnail.x) <= 1 &&
            Math.abs(snapshot.copy.x - snapshot.previousCopy.x) <= 1,
          `${width}px sponsored articles align with adjacent articles`,
        );
      }
      measurements.push(snapshot);
      if (output)
        await page.screenshot({
          path: `${output}-sponsor-${format}-${width}.png`,
          animations: "disabled",
        });
    }
  }
  return measurements;
}

async function checkStackedBadgeContrast(badge) {
  const result = await badge.evaluate((badge) => {
    const originalDark = document.documentElement.classList.contains("dark");
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const rgb = (css) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = css;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data].map((value) => value / 255);
    };
    const over = (foreground, background) => [
      ...foreground
        .slice(0, 3)
        .map((value, index) => value * foreground[3] + background[index] * (1 - foreground[3])),
      1,
    ];
    const luminance = (color) =>
      color
        .slice(0, 3)
        .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
        .reduce((total, value, index) => total + value * [0.2126, 0.7152, 0.0722][index], 0);
    const values = {};
    const transition = badge.style.transition;
    badge.style.transition = "none";
    for (const [theme, dark] of [
      ["light", false],
      ["dark", true],
    ]) {
      document.documentElement.classList.toggle("dark", dark);
      const style = getComputedStyle(badge);
      const surface =
        badge.closest(".partner-placement-link") ?? badge.closest(".partner-placement");
      const base = over(
        rgb(getComputedStyle(surface).backgroundColor),
        over(rgb(getComputedStyle(document.body).backgroundColor), [1, 1, 1, 1]),
      );
      const background = over(rgb(style.backgroundColor), base);
      const foreground = over(rgb(style.color), background);
      const levels = [luminance(foreground), luminance(background)].sort((a, b) => a - b);
      values[theme] = {
        foreground: style.color,
        background: style.backgroundColor,
        ratio: (levels[1] + 0.05) / (levels[0] + 0.05),
      };
    }
    document.documentElement.classList.toggle("dark", originalDark);
    badge.style.transition = transition;
    return values;
  });
  for (const [theme, { ratio }] of Object.entries(result))
    assert.ok(
      ratio >= 4.5,
      `The ${theme} Sponsored label has readable text contrast (${ratio.toFixed(2)}:1)`,
    );
  return result;
}

async function checkStackedSponsorLink(page, sponsorship) {
  await page.bringToFront();
  const link = page.getByRole("link", { name: sponsorship.product.name, exact: true });
  await link.scrollIntoViewIfNeeded();
  assert.equal(await link.getAttribute("href"), sponsorship.product.url);
  assert.equal(await link.getAttribute("target"), "_blank");
  assert.ok((await link.getAttribute("rel")).split(/\s+/).includes("nofollow"));
  await link.evaluate((node) =>
    node.addEventListener("click", (event) => event.preventDefault(), { once: true }),
  );
  const reported = page.waitForRequest((request) => {
    if (!request.url().endsWith("/api/v1/partner-tracking/events") || request.method() !== "POST")
      return false;
    const body = request.postDataJSON();
    return body.kind === "click" && body.receipt === sponsorship.product.receipt;
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.keyboard.press("Tab");
  await link.focus();
  const focus = await link.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      visible: node.matches(":focus-visible"),
      outline: parseFloat(style.outlineWidth),
      outlineStyle: style.outlineStyle,
      transition: style.transitionDuration,
    };
  });
  assert.equal(focus.visible, true, "Keyboard navigation makes the card focus visible");
  assert.ok(focus.outline >= 2 && focus.outlineStyle !== "none");
  assert.ok(
    focus.transition.split(",").every((value) => parseFloat(value) === 0),
    "Reduced motion removes the border transition",
  );
  await page.keyboard.press("Enter");
  await (await reported).response();
}

async function checkStackedGeometry(page, width) {
  const viewport = page.viewportSize();
  if (viewport?.width !== width || viewport?.height !== 1000)
    await page.setViewportSize({ width, height: 1000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const snapshot = await page
    .locator(".article-stacked")
    .first()
    .evaluate((container) => {
      const rows = [...container.querySelectorAll(".article-stacked-row")];
      const box = (element) => {
        const bounds = element.getBoundingClientRect();
        return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
      };
      const style = (element) => {
        const computed = getComputedStyle(element);
        return {
          fontSize: parseFloat(computed.fontSize),
          lineHeight: parseFloat(computed.lineHeight),
        };
      };
      return {
        viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        container: box(container),
        rows: rows.slice(0, 2).map((row) => ({
          bounds: box(row),
          title: box(row.querySelector(".article-stacked-title")),
          titleStyle: style(row.querySelector(".article-stacked-title")),
          metaStyle: style(row.querySelector(".article-stacked-meta")),
          description: row.querySelector(".article-stacked-description")
            ? box(row.querySelector(".article-stacked-description"))
            : null,
          actions: box(row.querySelector(".article-stacked-actions")),
          thumbnail: box(row.querySelector(".article-stacked-thumbnail")),
          divider: getComputedStyle(row).borderTopWidth,
        })),
      };
    });
  assert.ok(snapshot.documentWidth <= width + 1, `${width}px Stacked rows fit without overflow`);
  assert.ok(
    snapshot.container.x >= -1 && snapshot.container.x + snapshot.container.width <= width + 1,
  );
  assert.equal(snapshot.rows.length, 2);
  for (const row of snapshot.rows) {
    assert.ok(Math.abs(row.bounds.width - snapshot.container.width) <= 2);
    assert.ok(
      row.titleStyle.fontSize > row.metaStyle.fontSize,
      "Titles lead the metadata hierarchy",
    );
    assert.ok(row.thumbnail.width > 0 && row.thumbnail.height > 0);
    assert.ok(row.title.x >= row.bounds.x - 1 && row.title.x + row.title.width <= width + 1);
    assert.ok(row.actions.x >= row.bounds.x - 1 && row.actions.x + row.actions.width <= width + 1);
  }
  assert.ok(
    snapshot.rows[1].bounds.y >= snapshot.rows[0].bounds.y + snapshot.rows[0].bounds.height - 1,
  );
  assert.ok(parseFloat(snapshot.rows[1].divider) > 0, "Stacked rows have a shared divider");
  assert.ok(snapshot.rows[0].description.height > 0, "Long descriptions remain visible");
  assert.equal(snapshot.rows[1].description, null, "Missing descriptions leave no empty paragraph");
  return snapshot;
}

async function checkStackedTouchTargets(page, output) {
  const cdp = await page.context().newCDPSession(page);
  try {
    // Set device metrics before pointer emulation; a viewport update also updates CDP metrics.
    await page.setViewportSize({ width: 320, height: 1000 });
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    await cdp.send("Emulation.setEmitTouchEventsForMouse", {
      enabled: true,
      configuration: "mobile",
    });
    await page.waitForFunction(() => {
      if (!matchMedia("(pointer: coarse)").matches) return false;
      const actions = [
        ...document.querySelectorAll(
          ".article-stacked-row:first-child .article-stacked-actions a, .article-stacked-row:first-child .article-stacked-actions button",
        ),
      ];
      return (
        actions.length >= 4 &&
        actions.every((action) => {
          const bounds = action.getBoundingClientRect();
          return bounds.width >= 44 && bounds.height >= 44;
        })
      );
    });
    const geometry = await checkStackedGeometry(page, 320);
    const measurement = await page
      .locator(".article-stacked-row")
      .first()
      .locator(".article-stacked-actions a, .article-stacked-actions button")
      .evaluateAll((elements) => ({
        coarse: matchMedia("(pointer: coarse)").matches,
        targets: elements.map((element) => {
          const bounds = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return {
            name: element.getAttribute("aria-label") ?? element.textContent,
            width: bounds.width,
            height: bounds.height,
            minWidth: style.minWidth,
            minHeight: style.minHeight,
          };
        }),
      }));
    assert.equal(
      measurement.coarse,
      true,
      "Touch targets are measured with a coarse pointer after viewport layout",
    );
    const { targets } = measurement;
    assert.ok(targets.length >= 4, "The touch measurement includes all available row actions");
    for (const target of targets)
      assert.ok(
        target.width >= 44 && target.height >= 44,
        `${target.name} has a 44px touch target (${target.width}×${target.height}px)`,
      );
    if (output) await page.screenshot({ path: `${output}-320-touch.png`, animations: "disabled" });
    return { pointer: "coarse", geometry, targets };
  } finally {
    await cdp.send("Emulation.setEmitTouchEventsForMouse", { enabled: false });
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
    await cdp.detach();
  }
}

/** Run against the actual website and both built extension readers. */
export async function checkStackedReader(page, target, output, { signedIn = false } = {}) {
  const originalViewport = page.viewportSize();
  const sponsorship = await mockStackedPlacements(page);
  const firstRow = page.locator(".article-stacked-row:visible").first();
  await page.goto(target);
  await setArticleView(page, "stacked", { returnTo: target, keyboard: true });
  await firstRow.waitFor();
  assert.equal(page.url(), target, "Stacked selection retains the requested feed filters");
  assert.equal(await page.locator(".article-card:visible, .article-list-row:visible").count(), 0);
  assert.equal(
    await page.getByRole("region", { name: "Articles in stacked view", exact: true }).count(),
    1,
  );
  assert.equal(await firstRow.locator(".article-stacked-title").textContent(), stackedReaderTitle);
  assert.equal(
    await firstRow.locator(".article-stacked-description").textContent(),
    stackedReaderDescription,
  );
  const second = page.locator(".article-stacked-row").nth(1);
  assert.match(await second.textContent(), /fallback-publisher\.example\.test/);
  assert.equal(await second.locator(".image-placeholder").count(), 1);
  assert.equal(await second.locator(".article-stacked-summary").count(), 0);
  assert.equal(await second.locator(".article-stacked-topic").count(), 0);
  await page.waitForFunction(() => {
    const image = document.querySelector(".article-stacked-thumbnail img");
    return image?.complete && image.naturalWidth > 0;
  });
  const measurements = [];
  if (output) await mkdir(dirname(output), { recursive: true });
  for (const width of [320, 768, 1440]) {
    measurements.push(await checkStackedGeometry(page, width));
    if (output) await page.screenshot({ path: `${output}-${width}.png`, animations: "disabled" });
  }
  measurements.push(await checkStackedTouchTargets(page, output));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await firstRow.waitFor();
  assert.equal(page.url(), target);
  if (!signedIn)
    assert.equal(
      await page.evaluate(
        () => JSON.parse(localStorage.getItem("devfeed:feed-views:guest")).views.latest,
      ),
      "stacked",
      "Guest Stacked selection survives a reload",
    );
  await openArticleViewSettings(page, { feed: "latest" });
  assert.equal(await page.getByRole("radio", { name: "Stacked", exact: true }).isChecked(), true);
  await page.goto(target);
  await firstRow.waitFor();
  const title = firstRow.locator(".article-stacked-title");
  const articleHref = await title.getAttribute("href");
  const bookmark = firstRow.locator(".bookmark-button");
  if (signedIn) {
    await bookmark.waitFor();
    const initial = await bookmark.getAttribute("aria-pressed");
    await bookmark.click();
    await firstRow
      .locator(`.bookmark-button[aria-pressed="${initial === "true" ? "false" : "true"}"]`)
      .waitFor();
  }
  await page.mouse.move(0, 0);
  await page.keyboard.press("Tab");
  await title.focus();
  assert.equal(await title.evaluate((element) => element.matches(":focus-visible")), true);
  await page.keyboard.press("Enter");
  const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
  await preview.waitFor();
  await preview.getByRole("heading", { name: "AI overview", exact: true }).waitFor();
  await page.locator("dialog.article-modal:modal").waitFor();
  await page.locator(".truncated-text-tooltip:visible").waitFor({ state: "hidden" });
  await page.keyboard.press("Escape");
  await preview.waitFor({ state: "hidden" });
  await page.waitForURL(target);
  assert.equal(await title.evaluate((element) => element === document.activeElement), true);
  await firstRow.locator(".article-stacked-summary").click();
  await preview.waitFor();
  await preview.getByRole("heading", { name: "AI overview", exact: true }).waitFor();
  await preview.getByRole("button", { name: "Close preview", exact: true }).click();
  await preview.waitFor({ state: "hidden" });
  await page.waitForURL(target);
  assert.equal(await title.getAttribute("href"), articleHref);
  const share = firstRow.getByRole("button", { name: /^Share article:/ });
  await share.focus();
  await page.keyboard.press("Enter");
  const popover = page.getByRole("dialog", { name: "Share article", exact: true });
  await popover.waitFor();
  const sharedLink = await popover
    .getByRole("link", { name: "Share on X (opens in a new tab)", exact: true })
    .getAttribute("href");
  const publicUrl = new URL(sharedLink).searchParams.get("url");
  const destination = new URL(articleHref, "https://devfeed.tech");
  const articlePath = destination.hash.startsWith("#/")
    ? destination.hash.slice(1).split("?")[0]
    : destination.pathname;
  assert.equal(new URL(publicUrl).pathname, articlePath);
  await page.keyboard.press("Escape");
  await popover.waitFor({ state: "hidden" });
  assert.equal(await share.evaluate((element) => element === document.activeElement), true);
  const beforePagination = await page
    .locator(".article-stacked-row:not(.partner-placement)")
    .count();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.locator('.article-stacked-title[href$="/articles/stacked-reader-12"]').waitFor();
  assert.equal(await page.locator(".article-stacked-row:not(.partner-placement)").count(), 13);
  assert.ok(beforePagination <= 13);
  assert.equal(page.url(), target, "Appending another page keeps the filtered feed route");
  await page.getByRole("combobox", { name: "Sort by", exact: true }).click();
  await page.getByRole("option", { name: "Newest", exact: true }).click();
  await page.waitForURL(
    (url) =>
      new URL(
        url.hash.startsWith("#/") ? url.hash.slice(1) : url.href,
        "https://devfeed.tech",
      ).searchParams.get("sort") === "newest",
  );
  await firstRow.waitFor();
  assert.equal(await page.locator(".article-card:visible, .article-list-row:visible").count(), 0);
  await page.getByRole("combobox", { name: "Sort by", exact: true }).click();
  await page.getByRole("option", { name: "Most liked", exact: true }).click();
  await page.waitForURL(target);
  await firstRow.waitFor();
  await setArticleView(page, "compact", { returnTo: target });
  const compactBookmark = page.locator(".article-list-row").first().locator(".bookmark-button");
  await compactBookmark.waitFor();
  if (signedIn)
    await page
      .locator('.article-list-row:first-child .bookmark-button[aria-pressed="true"]')
      .waitFor();
  await setArticleView(page, "stacked", { returnTo: target });
  await firstRow.waitFor();
  if (signedIn) await firstRow.locator('.bookmark-button[aria-pressed="true"]').waitFor();
  const sponsoredMeasurements = await checkStackedSponsorship(page, output);
  await checkStackedSponsorLink(page, sponsorship);
  if (output)
    await writeFile(
      `${output}.json`,
      JSON.stringify(
        {
          passed: true,
          signedIn,
          measurements,
          sponsoredMeasurements,
          trackingEvents: sponsorship.events,
        },
        null,
        2,
      ),
    );
  await sponsorship.cleanup();
  if (originalViewport) await page.setViewportSize(originalViewport);
}
