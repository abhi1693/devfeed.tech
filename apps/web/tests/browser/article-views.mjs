import assert from "node:assert/strict";

/** Exercise the same layout control in the website and actual unpacked extensions. */
export async function checkArticleViews(page, target, output, routes = []) {
  await page.goto(target);
  const grid = page.getByRole("button", { name: "Grid view", exact: true });
  const list = page.getByRole("button", { name: "List view", exact: true });
  await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
  await grid.click();
  await page.locator(".article-card").first().waitFor();
  await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
  const originalLinks = await page
    .locator(".card-open-link")
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  const cardHeight = (await page.locator(".article-card").first().boundingBox()).height;
  const reloads = [];
  const record = (request) => {
    const url = new URL(request.url());
    if (
      /\/api\/v1\/(?:feed|user\/(?:feed|bookmarks|trending))$/.test(url.pathname) &&
      !url.searchParams.get("cursor")
    )
      reloads.push(url.href);
  };
  page.on("request", record);
  try {
    await list.focus();
    await page.keyboard.press("Enter");
    await page.locator(".article-table tbody tr").first().waitFor();
    assert.equal(await list.getAttribute("aria-pressed"), "true");
    const links = await page
      .locator(".article-list-title")
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
    assert.ok(
      originalLinks.every((href) => links.includes(href)),
      "Switching retains every loaded article",
    );
    assert.ok(
      (await page.locator(".article-list-row").first().boundingBox()).height < cardHeight / 2,
      "List rows are substantially shorter than cards",
    );
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        true,
        `List must fit at ${width}px`,
      );
      const button = await list.boundingBox();
      assert.ok(button.width >= 44 && button.height >= 44, "Layout buttons retain touch targets");
      if (width === 320) {
        const bookmark = await page
          .locator(".article-table .bookmark-button")
          .first()
          .boundingBox();
        assert.ok(
          bookmark.width >= 44 && bookmark.height >= 44,
          "Compact rows retain mobile bookmarks",
        );
        assert.equal(
          await page.locator(".article-table .article-engagement").first().isVisible(),
          false,
        );
        assert.equal(await page.locator(".article-list-mobile-source").first().isVisible(), true);
      }
      if (output)
        await page.screenshot({ path: `${output}-list-${width}.png`, animations: "disabled" });
      const dark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", !value),
        dark,
      );
      if (output)
        await page.screenshot({
          path: `${output}-list-${width}-alternate-theme.png`,
          animations: "disabled",
        });
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", value),
        dark,
      );
    }
    const row = await page.locator(".article-list-row").first().elementHandle();
    await page.locator(".article-list-title").first().click();
    await page.locator("#article-preview-title").waitFor();
    assert.equal(await row.evaluate((node) => node.isConnected), true);
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.locator("dialog.article-modal").waitFor({ state: "detached" });
    assert.equal(await row.evaluate((node) => node.isConnected), true);
    assert.equal(
      reloads.length,
      0,
      `Layout and previews do not reload the current feed: ${JSON.stringify(reloads)}`,
    );
    await row.dispose();
  } finally {
    page.off("request", record);
  }
  for (const route of routes) {
    await page.goto(route);
    await page.locator(".article-table tbody tr").first().waitFor();
    assert.equal(await list.getAttribute("aria-pressed"), "true", "Layout is shared across feeds");
  }
  await page.reload();
  await page.locator(".article-table tbody tr").first().waitFor();
  await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
  await grid.click();
  await page.locator(".article-card").first().waitFor();
  await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
  assert.equal(await grid.getAttribute("aria-pressed"), "true");
  if (output) await page.screenshot({ path: `${output}-grid.png`, animations: "disabled" });
}
