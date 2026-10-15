import assert from "node:assert/strict";

export const adFormats = ["feed", "homepage_sponsorship", "sponsored_content", "engagement"];

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
    description: "Monitor production APIs and diagnose reliability problems.",
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

export async function checkHomepageSponsorshipPreview(page, preview, { product, toggle }) {
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
        const layout = await preview.locator("article").evaluate((card, productName) => {
          const title = [...card.querySelectorAll("p")].find(
            (element) => element.textContent === productName,
          );
          const host = title?.nextElementSibling;
          const copy = title?.parentElement;
          const badge = [...card.querySelectorAll("span")].find(
            (element) => element.textContent === "Sponsored",
          );
          const logo = card.querySelector("img")?.parentElement;
          if (!title || !host || !copy || !badge || !logo) return null;
          const bounds = (element) => {
            const { x, y, width, height, right } = element.getBoundingClientRect();
            return { x, y, width, height, right };
          };
          return {
            card: bounds(card),
            logo: bounds(logo),
            title: bounds(title),
            copy: bounds(copy),
            badge: bounds(badge),
            titleOverflow: title.scrollWidth > title.clientWidth,
          };
        }, name);
        const label = `${width}px ${name === originalName ? "normal" : "long"} homepage preview`;
        assert.ok(layout, `${label} retains its logo, title, destination and disclosure`);
        assert.ok(layout.logo.right <= layout.copy.x, `${label} places the logo before the copy`);
        assert.ok(layout.copy.width >= 80, `${label} keeps the product name readable`);
        assert.ok(layout.badge.width > 0 && layout.badge.height > 0, `${label} shows Sponsored`);
        assert.ok(layout.badge.right <= layout.card.right - 8, `${label} contains Sponsored`);
        assert.ok(
          layout.badge.y + layout.badge.height <= layout.card.y + layout.card.height - 8,
          `${label} keeps Sponsored inside the card`,
        );
        assert.ok(layout.card.x >= 0 && layout.card.right <= width, `${label} fits the viewport`);
        const center = (item) => item.y + item.height / 2;
        assert.ok(
          Math.abs(center(layout.logo) - center(layout.copy)) <= 2,
          `${label} vertically aligns the logo and product copy`,
        );
        if (Math.abs(center(layout.badge) - center(layout.copy)) <= 2) {
          assert.ok(
            layout.copy.right <= layout.badge.x,
            `${label} places Sponsored after the copy`,
          );
          assert.ok(layout.card.height <= 88, `${label} remains a compact single row`);
        } else {
          assert.ok(
            layout.badge.y >=
              Math.max(layout.logo.y + layout.logo.height, layout.copy.y + layout.copy.height),
            `${label} wraps Sponsored below the logo and copy without overlap`,
          );
          assert.ok(
            Math.abs(layout.badge.right - layout.copy.right) <= 2,
            `${label} right-aligns the wrapped Sponsored badge`,
          );
          assert.ok(layout.card.height <= 120, `${label} stays compact when Sponsored wraps`);
        }
        assert.ok(layout.title.height <= 28, `${label} keeps the title on one line`);
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
