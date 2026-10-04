import assert from "node:assert/strict";

export function notificationFixture() {
  return {
    items: [
      {
        id: "notif_" + "a".repeat(26),
        source: "notification",
        category: "system.notice",
        payload: { title: "Account notice", body: "Information only" },
        occurred_at: "2026-09-30T10:00:00Z",
        read: false,
        archived: false,
      },
    ],
    next_cursor: null,
  };
}

/** Wait for a stable target before dispatching native touch events. */
export async function touchReaderTarget(cdp, locator) {
  // Trial actions re-resolve a detached locator without sending a desktop click.
  await locator.click({ trial: true });
  const box = await locator.boundingBox();
  assert.ok(box, "touch target has visible bounds");
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
  });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

/** Exercise actual touch events, not desktop clicks in a narrow viewport. */
export async function checkReaderInteractions(
  page,
  destination,
  screenshot,
  { recover = false } = {},
) {
  const original = page.viewportSize();
  const cdp = await page.context().newCDPSession(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const tap = (locator) => touchReaderTarget(cdp, locator);
  const nav = page.locator(".mobile-nav");
  const free = async () => {
    assert.notEqual(
      await page.locator("body").evaluate((el) => getComputedStyle(el).pointerEvents),
      "none",
    );
    assert.equal(await page.locator("dialog[open]").count(), 0);
  };
  try {
    await page.goto(destination("/latest"));
    await page.getByRole("button", { name: /^User menu:/ }).waitFor();
    const bell = page.getByRole("button", { name: /^Notifications/ });
    for (let attempt = 0; attempt < 3; attempt++) {
      await tap(bell);
      await tap(page.getByText("Account notice", { exact: true }));
      await page.locator(".devfeed-inbox-panel").waitFor();
      await tap(bell);
      await page.locator(".devfeed-inbox-panel").waitFor({ state: "hidden" });
      await free();
      await tap(nav.getByRole("link", { name: "Sources", exact: true }));
      await page.waitForURL(destination("/sources"));
      await page.getByRole("heading", { name: "Sources", exact: true }).waitFor();
      await tap(nav.getByRole("link", { name: "Topics", exact: true }));
      await page.waitForURL(destination("/topics"));
      await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
    }
    // Switching away from an open account menu must work with the first tap.
    await tap(page.getByRole("button", { name: /^User menu:/ }));
    await page.getByRole("menu").waitFor();
    await free();
    await tap(nav.getByRole("link", { name: "Sources", exact: true }));
    await page.waitForURL(destination("/sources"), { timeout: 3000 });
    await page.getByRole("menu").waitFor({ state: "hidden" });
    await free();

    // A history/router operation that never completes must not retain modality.
    await tap(nav.getByRole("link", { name: "Latest", exact: true }));
    await page.waitForURL(destination("/latest"));
    await tap(page.locator(".card-open-link").first());
    await page.getByRole("dialog", { name: "Article preview" }).waitFor();
    await page.evaluate(() => {
      window.__restoreReaderBack = window.history.back;
      window.history.back = () => {};
    });
    await tap(page.getByRole("button", { name: "Close preview" }));
    await page
      .getByRole("dialog", { name: "Article preview" })
      .waitFor({ state: "hidden", timeout: 1000 });
    await page.evaluate(() => {
      window.history.back = window.__restoreReaderBack;
    });
    await free();
    await tap(nav.getByRole("link", { name: "Sources", exact: true }));
    await page.waitForURL(destination("/sources"));
    await page.getByRole("heading", { name: "Sources", exact: true }).waitFor();
    await page.screenshot({ path: screenshot, animations: "disabled" });

    if (recover) {
      const held = [];
      const fullLoads = [];
      const matcher = (url) => ["/sources", "/topics"].includes(url.pathname);
      const block = async (route) => {
        if (route.request().headers().rsc === "1") {
          await new Promise((release) => held.push(release));
          await route.abort().catch(() => {});
        } else {
          if (route.request().isNavigationRequest())
            fullLoads.push(new URL(route.request().url()).pathname);
          await route.continue();
        }
      };
      await page.goto(destination("/latest"));
      await page.getByRole("button", { name: /^User menu:/ }).waitFor();
      await page.route(matcher, block);
      try {
        await tap(nav.getByRole("link", { name: "Sources", exact: true }));
        await page.getByRole("status").getByText("Loading page…").waitFor();
        await tap(nav.getByRole("link", { name: "Topics", exact: true }));
        await page.waitForURL(destination("/topics"), { timeout: 15000 });
        await page.getByRole("heading", { name: "Explore topics", exact: true }).waitFor();
        assert.deepEqual(
          fullLoads,
          ["/topics"],
          "recovery loads only the latest requested destination",
        );
        await free();
      } finally {
        held.forEach((release) => release());
        await page.unroute(matcher, block);
      }
    }
  } finally {
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
    await cdp.detach();
    await page.setViewportSize(original);
    await page.emulateMedia({ reducedMotion: "reduce" });
  }
}
