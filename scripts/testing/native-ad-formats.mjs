import assert from "node:assert/strict";

export const adFormats = ["feed", "header_sponsorship", "sponsored_content", "engagement"];

export const sponsoredArticle = {
  id: "99999999-9999-4999-8999-999999999999",
  slug: "reliable-api-checks",
  title: "Build reliable API checks",
  canonical_url: "https://publisher.example/reliable-api-checks",
  summary: "A practical guide to checking API reliability in production.",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: "Engineering team",
  content_type: "tutorial",
  content_format: "article",
  language: "en",
  published_at: "2026-10-10T10:00:00Z",
  feed_at: "2026-10-10T10:00:00Z",
  tags: [],
  topics: [
    {
      id: "77777777-7777-4777-8777-777777777777",
      name: "Kubernetes",
      slug: "kubernetes",
      kind: "platform",
      role: "primary",
      relevance: 1,
    },
  ],
  sources: [],
  sponsor_names: [],
};

export function creativeProduct(product) {
  return {
    ...product,
    tagline: "Monitor production APIs and diagnose reliability problems.",
    logo_url: "https://assets.partner.example/logo.svg",
    image_url: "https://assets.partner.example/creative.svg",
  };
}

export async function installCreativeMedia(context) {
  await context.route("https://assets.partner.example/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: route.request().url().endsWith("logo.svg")
        ? '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" rx="10" fill="#f59e0b"/></svg>'
        : '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="315"><rect width="600" height="315" fill="#1e293b"/><text x="40" y="170" fill="white" font-size="48">API Checker</text></svg>',
    }),
  );
}

export async function checkHeaderSponsorshipPreview(page, preview, { product, toggle }) {
  const originalName = product.name;
  const originalViewport = page.viewportSize();
  const longName = "API Checker Cloud developer workflow observability and reliability platform "
    .repeat(3)
    .slice(0, 200);
  async function refresh() {
    await toggle.click();
    await preview.waitFor({ state: "detached" });
    await toggle.click();
    await preview.getByText(product.name, { exact: true }).waitFor();
  }
  try {
    for (const name of [originalName, longName]) {
      if (product.name !== name) {
        product.name = name;
        await refresh();
      }
      for (const width of [1280, 390, 320]) {
        await page.setViewportSize({ width, height: width < 1024 ? 844 : 1100 });
        const layout = await preview.locator("article").evaluate(
          (card, product) => {
            const title = [...card.querySelectorAll("p")].find(
              (element) => element.textContent === product.name,
            );
            const tagline = [...card.querySelectorAll("p")].find(
              (element) => element.textContent === product.tagline,
            );
            const credit = title?.closest("[title]");
            const badge = [...card.querySelectorAll("span")].find(
              (element) => element.textContent === "Sponsored",
            );
            const logo = card.querySelector("img")?.parentElement;
            const navigation = [...card.querySelectorAll("span[aria-hidden]")].filter((element) =>
              ["DevFeed", "Search", "Sign in"].includes(element.textContent),
            );
            if (!title || !tagline || !credit || !badge || !logo || navigation.length !== 3)
              return null;
            const bounds = (element) => {
              const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
              return { x, y, width, height, right, bottom };
            };
            return {
              card: bounds(card),
              logo: bounds(logo),
              title: bounds(title),
              tagline: bounds(tagline),
              credit: bounds(credit),
              navigation: navigation.map(bounds),
              badge: bounds(badge),
              titleOverflow: title.scrollWidth > title.clientWidth,
              destination: credit.getAttribute("title"),
              text: card.textContent,
              interactiveControls: card.querySelectorAll("button,input,select,a").length,
            };
          },
          { name, tagline: product.tagline },
        );
        const label = `${width}px ${name === originalName ? "normal" : "long"} header preview`;
        assert.ok(layout, `${label} shows its credit within an example global header`);
        assert.ok(layout.logo.right <= layout.title.x, `${label} places the logo before the name`);
        assert.equal(layout.logo.width, 32, `${label} uses a recognizable header-sized logo`);
        assert.ok(layout.title.width >= 80, `${label} keeps the product name readable`);
        assert.ok(layout.badge.width > 0 && layout.badge.height > 0, `${label} shows Sponsored`);
        assert.ok(layout.badge.right <= layout.card.right - 8, `${label} contains Sponsored`);
        assert.ok(
          layout.badge.y + layout.badge.height <= layout.card.y + layout.card.height - 8,
          `${label} keeps Sponsored inside the card`,
        );
        assert.ok(layout.card.x >= 0 && layout.card.right <= width, `${label} fits the viewport`);
        assert.ok(
          layout.credit.x >= layout.card.x && layout.credit.right <= layout.card.right,
          `${label} contains its compact credit`,
        );
        assert.equal(
          await preview.locator("article").getAttribute("aria-label"),
          "Desktop navigation preview",
          `${label} identifies the desktop-only placement context`,
        );
        for (const peer of layout.navigation)
          assert.ok(
            peer.right <= layout.credit.x + 1 ||
              peer.x >= layout.credit.right - 1 ||
              peer.bottom <= layout.credit.y + 1 ||
              peer.y >= layout.credit.bottom - 1,
            `${label} never overlaps example brand, search or account controls`,
          );
        const center = (item) => item.y + item.height / 2;
        assert.ok(
          Math.abs(center(layout.logo) - (layout.title.y + layout.tagline.bottom) / 2) <= 3,
          `${label} vertically aligns the logo with the product name and tagline`,
        );
        assert.ok(
          layout.tagline.width >= 80 && layout.tagline.height > 0 && layout.tagline.height <= 36,
          `${label} keeps the short product explanation readable`,
        );
        assert.ok(
          layout.tagline.y >= layout.title.bottom - 1 &&
            layout.tagline.x >= layout.logo.right &&
            layout.tagline.right <= layout.credit.right + 1,
          `${label} places its tagline below the name without overlapping the logo`,
        );
        if (Math.abs(center(layout.badge) - center(layout.title)) <= 2) {
          assert.ok(
            layout.title.right <= layout.badge.x,
            `${label} places Sponsored after the name`,
          );
        } else {
          assert.ok(
            layout.badge.y >= Math.max(layout.logo.bottom, layout.title.bottom),
            `${label} wraps Sponsored within the header example without overlap`,
          );
        }
        assert.ok(layout.card.height <= 144, `${label} remains a compact header preview`);
        assert.ok(layout.title.height <= 28, `${label} keeps the title on one line`);
        assert.equal(
          layout.interactiveControls,
          0,
          `${label} does not invent working navigation controls`,
        );
        assert.ok(
          [product.product_url, product.listing_url]
            .filter(Boolean)
            .some((url) => new URL(url).hostname.replace(/^www\./, "") === layout.destination),
          `${label} retains the selected destination as hover context`,
        );
        assert.ok(
          layout.text.includes(product.tagline),
          `${label} explains the sponsored product with its short tagline`,
        );
        if (name === longName)
          assert.equal(layout.titleOverflow, true, `${label} exercises actual title truncation`);
      }
    }
  } finally {
    if (product.name !== originalName) {
      product.name = originalName;
      await refresh();
    }
    if (originalViewport) await page.setViewportSize(originalViewport);
  }
}
