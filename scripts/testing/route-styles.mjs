import assert from "node:assert/strict";

const observed = new WeakMap();

/** Observe rendered frames, including the gap while a newly requested stylesheet is delayed. */
export async function monitorRouteStyles(page) {
  const failures = [];
  observed.set(page, failures);
  await page.exposeFunction("reportRouteStyleFailure", (failure) => failures.push(failure));
  const observe = () => {
    window.__routeStyleFailures = [];
    const expectations = [
      [".article-grid", "display", "grid"],
      [".topic-grid", "display", "grid"],
      [".preview-modal", "display", "grid"],
      [".article-modal", "borderTopWidth", "1px"],
      ['dialog[aria-label="Your dev card preview"]', "borderTopWidth", "1px"],
      [".mcp-layout", "display", "grid"],
      [".profile-settings-nav", "display", "flex"],
      [".profile-field-grid", "display", "grid"],
      [".public-profile", "display", "grid"],
    ];
    const frame = () => {
      for (const [selector, property, expected] of expectations) {
        const node = document.querySelector(selector);
        if (!node || !node.getClientRects().length) continue;
        const actual = getComputedStyle(node)[property];
        if (actual !== expected && window.__routeStyleFailures.length < 20) {
          window.__routeStyleFailures.push({ selector, property, expected, actual });
          window.reportRouteStyleFailure({
            selector,
            property,
            expected,
            actual,
            url: location.href,
          });
        }
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  };
  await page.addInitScript(observe);
  // Extension hash navigation keeps the current document alive.
  await page.evaluate(observe);
}

export async function assertRouteStyles(page) {
  assert.deepEqual(observed.get(page), [], "Every visible frame has its route styles");
}

export async function checkRouteStyles(page, destination, extension = false) {
  await monitorRouteStyles(page);
  if (!extension) {
    const startup = await page.context().newPage();
    try {
      await startup.route(/\/_next\/static\/.*\.js(?:\?.*)?$/, (route) => route.abort());
      await startup.goto(destination("/latest"), { waitUntil: "domcontentloaded" });
      await startup.locator(".article-card").first().waitFor();
      const styles = await startup.evaluate(() =>
        [...document.styleSheets]
          .flatMap((sheet) => [...sheet.cssRules].map((rule) => rule.cssText))
          .join("\n"),
      );
      assert.ok(
        !styles.includes("--promo-card-width"),
        "The optional dev card dialog stylesheet does not block the server-rendered feed",
      );
    } finally {
      await startup.close();
    }
  }
  if (!extension)
    await page.route(/\/_next\/static\/.*\.css(?:\?.*)?$/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 200));
      await route.continue();
    });
  for (const width of [1440, 412]) {
    await page.setViewportSize({ width, height: 940 });
    await page.goto(destination("/latest"));
    await page.locator(".article-card").first().waitFor();
    const feedStyles = () =>
      page.evaluate(() => {
        const properties = [
          "display",
          "padding",
          "fontSize",
          "lineHeight",
          "borderRadius",
          "backgroundColor",
        ];
        return [
          ".main-content",
          ".topbar",
          ".article-grid",
          ".article-card",
          ".card-copy",
          ".feed-toolbar",
        ].map((selector) => {
          const style = getComputedStyle(document.querySelector(selector));
          return [
            selector,
            Object.fromEntries(properties.map((property) => [property, style[property]])),
          ];
        });
      });
    const initial = await feedStyles();
    if (!extension) {
      const styles = await page.evaluate(() =>
        [...document.styleSheets]
          .flatMap((sheet) => [...sheet.cssRules].map((rule) => rule.cssText))
          .join("\n"),
      );
      for (const selector of [".profile-direct", ".mcp-page", ".public-profile", ".article-modal"])
        assert.ok(
          !styles.includes(`${selector} {`),
          `${selector} is absent from a fresh anonymous feed`,
        );
    }
    await page.locator(".card-open-link").first().click();
    const dialog = page.getByRole("dialog", { name: "Article preview", exact: true });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "Close preview", exact: true }).click();
    await dialog.waitFor({ state: "detached" });
    await assertRouteStyles(page);
    for (const [path, selector] of [
      ["/topics", ".topic-grid"],
      ["/mcp", ".mcp-layout"],
      ["/latest", ".article-grid"],
    ]) {
      // Desktop uses the client router; mobile also exercises direct entries into each boundary.
      if (width > 800)
        await page.locator(`.sidebar a[href="${extension ? "#" : ""}${path}"]`).click();
      else {
        await assertRouteStyles(page);
        await page.goto(destination(path));
      }
      await page.locator(selector).first().waitFor();
      await assertRouteStyles(page);
    }
    assert.deepEqual(
      await feedStyles(),
      initial,
      "Visiting route styles preserves the feed's layout and theme",
    );
  }
}
