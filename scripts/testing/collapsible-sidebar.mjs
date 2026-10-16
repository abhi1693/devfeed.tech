import assert from "node:assert/strict";

export async function checkCollapsibleSidebar(page, screenshotPrefix) {
  const originalViewport = page.viewportSize();
  const sidebar = page.locator('.shared-sidebar[data-sidebar-layout="reader"]');
  const expand = page.getByRole("button", { name: "Expand sidebar", exact: true });
  const collapse = page.getByRole("button", { name: "Collapse sidebar", exact: true });
  const width = async (expected) => {
    await page.waitForFunction(
      (value) =>
        Math.abs(document.getElementById("reader-sidebar").getBoundingClientRect().width - value) <
        1,
      expected,
    );
  };
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    if (await collapse.count()) await collapse.click();
    await width(76);
    assert.equal(await sidebar.getAttribute("aria-label"), "Primary navigation");
    assert.equal(await expand.getAttribute("aria-controls"), "reader-sidebar");
    assert.equal(
      await sidebar.locator(".sidebar-legal").evaluate((node) => getComputedStyle(node).visibility),
      "hidden",
    );
    if (screenshotPrefix) await page.screenshot({ path: `${screenshotPrefix}-collapsed.png` });
    await expand.focus();
    await page.keyboard.press("Enter");
    await width(232);
    assert.equal(await collapse.getAttribute("aria-expanded"), "true");
    assert.equal(
      await page.evaluate(() => localStorage.getItem("devfeed:sidebar-expanded")),
      "true",
    );
    assert.equal(await sidebar.getByRole("navigation", { name: "Legal" }).isVisible(), true);
    const latest = sidebar.getByRole("link", { name: "Latest feed", exact: true });
    assert.equal(
      await latest.locator("span").evaluate((node) => getComputedStyle(node).opacity),
      "1",
    );
    if (screenshotPrefix) await page.screenshot({ path: `${screenshotPrefix}-expanded.png` });

    await page.reload();
    await collapse.waitFor();
    await width(232);
    // Native storage notifications update both the common control and reader page geometry.
    await page.evaluate(() => {
      localStorage.setItem("devfeed:sidebar-expanded", "false");
      window.dispatchEvent(
        new StorageEvent("storage", { key: "devfeed:sidebar-expanded", newValue: "false" }),
      );
    });
    await expand.waitFor();
    await width(76);
    await expand.click();
    await width(232);

    // Existing layout benchmarks set the root attribute directly without React state changes.
    await page.evaluate(() => {
      document.documentElement.dataset.sidebarState = "collapsed";
    });
    await width(76);
    await page.evaluate(() => {
      document.documentElement.dataset.sidebarState = "expanded";
    });
    await width(232);

    await page.setViewportSize({ width: 900, height: 900 });
    await width(208);
    await collapse.click();
    await width(72);
    await expand.click();
    await page.setViewportSize({ width: 1440, height: 300 });
    await width(232);
    const content = sidebar.locator(".shared-sidebar-content");
    assert.ok(
      await content.evaluate((node) => node.scrollHeight > node.clientHeight),
      "short desktop navigation scrolls independently",
    );
    const toggleBox = await collapse.boundingBox();
    assert.ok(
      toggleBox && toggleBox.y >= 72 && toggleBox.y + toggleBox.height <= 300,
      "collapse control stays visible below scrolling navigation",
    );
    await collapse.click();

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await sidebar.isVisible(), false);
    assert.equal(
      await page.getByRole("navigation", { name: "Mobile navigation" }).isVisible(),
      true,
    );
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    );
    if (screenshotPrefix) await page.screenshot({ path: `${screenshotPrefix}-mobile.png` });
  } finally {
    if (originalViewport) await page.setViewportSize(originalViewport);
  }
}
