import assert from "node:assert/strict";

export const thumbnailProfiles = [
  { width: 320, height: 823, dpr: 1 },
  { width: 412, height: 823, dpr: 1.75 },
  { width: 520, height: 900, dpr: 2 },
  { width: 611, height: 900, dpr: 1 },
  { width: 612, height: 900, dpr: 2 },
  { width: 800, height: 900, dpr: 1.75 },
  { width: 801, height: 900, dpr: 1 },
  { width: 992, height: 900, dpr: 1 },
  { width: 1200, height: 940, dpr: 1 },
  { width: 1201, height: 940, dpr: 1 },
  { width: 1350, height: 940, dpr: 1 },
  { width: 1350, height: 940, dpr: 2 },
  { width: 1920, height: 1080, dpr: 1 },
  { width: 2560, height: 1440, dpr: 1 },
];

export async function checkThumbnailSizing(page, href, sidebar = "collapsed") {
  const requests = [];
  const record = (request) => {
    if (request.resourceType() === "image" && /\/thumbnails\//.test(request.url()))
      requests.push(request.url());
  };
  await page.goto("about:blank");
  const session = await page.context().newCDPSession(page);
  await session.send("Network.clearBrowserCache");
  await session.detach();
  page.on("request", record);
  await page.goto(href);
  await page.locator(".article-card .card-image img").first().waitFor();
  if (sidebar === "expanded" && page.viewportSize().width > 800) {
    await page.getByRole("button", { name: "Expand sidebar" }).click();
    await page.waitForFunction(() => document.documentElement.dataset.sidebarState === "expanded");
  }
  // Inspect the eager cover and a separate lazy cover at their final CSS sizes.
  const results = [];
  for (const index of [0, 1]) {
    const image = page.locator(".article-card .card-image img").nth(index);
    await image.scrollIntoViewIfNeeded();
    await page.waitForFunction((index) => {
      const image = document.querySelectorAll(".article-card .card-image img")[index];
      return image?.complete && image.naturalWidth > 0;
    }, index);
    const measured = await image.evaluate((image) => {
      const rect = image.getBoundingClientRect();
      return {
        currentSrc: image.currentSrc,
        naturalWidth: image.naturalWidth,
        naturalHeight: image.naturalHeight,
        renderedWidth: rect.width,
        renderedHeight: rect.height,
        dpr: devicePixelRatio,
        sizes: image.sizes,
        loading: image.loading,
        priority: image.fetchPriority,
      };
    });
    const selected = Number(new URL(measured.currentSrc).pathname.match(/\/(\d+)\.webp$/)?.[1]);
    assert.ok([320, 480, 640, 768, 960].includes(selected), JSON.stringify(measured));
    assert.ok(measured.naturalWidth > 0 && measured.naturalHeight > 0);
    assert.ok(Math.abs(measured.renderedWidth / measured.renderedHeight - 16 / 9) < 0.01);
    const required = Math.min(960, measured.renderedWidth * measured.dpr);
    assert.ok(selected >= required - 1, `Sharpness: ${JSON.stringify(measured)}`);
    if (index === 0) {
      assert.equal(measured.loading, "eager");
      assert.equal(measured.priority, "high");
      assert.ok(!measured.sizes.startsWith("auto"));
      if (page.viewportSize().width === 1350 && measured.dpr === 1) assert.equal(selected, 480);
      if (page.viewportSize().width === 412 && measured.dpr === 1.75) assert.equal(selected, 768);
    } else {
      assert.equal(measured.loading, "lazy");
      assert.ok(measured.sizes.startsWith("auto, "));
      // Chromium can retain a larger cached candidate after a genuine layout change.
      if (sidebar === "collapsed")
        assert.equal(
          selected,
          [320, 480, 640, 768, 960].find((width) => width >= required - 1),
          JSON.stringify(measured),
        );
    }
    results.push({ sidebar, ...measured, selected });
  }
  if (sidebar === "collapsed") {
    for (const image of results) {
      const asset = image.currentSrc.slice(0, image.currentSrc.lastIndexOf("/"));
      assert.equal(
        requests.filter((url) => url.startsWith(`${asset}/`)).length,
        1,
        `One initial request per image: ${JSON.stringify(requests)}`,
      );
    }
  }
  page.off("request", record);
  if (
    page.viewportSize().width === 1350 &&
    page.viewportSize().height === 940 &&
    sidebar === "collapsed"
  ) {
    const pattern = "https://images.example.test/thumbnails/**";
    const context = page.context();
    // Keep the existing context interceptor installed. Removing a page interceptor
    // can forward its in-flight requests to the context while their callbacks run.
    const failThumbnail = (route) =>
      route.request().frame().page() === page ? route.abort() : route.fallback();
    await context.route(pattern, failThumbnail);
    try {
      await page.goto("about:blank");
      const cache = await context.newCDPSession(page);
      await cache.send("Network.clearBrowserCache");
      await cache.detach();
      await page.goto(href);
      await page.locator(".article-card .card-image .image-placeholder").first().waitFor();
      assert.equal(
        await page.locator(".article-card .card-image").first().locator("img").count(),
        0,
      );
    } finally {
      await context.unroute(pattern, failThumbnail);
    }
  }
  return results;
}
