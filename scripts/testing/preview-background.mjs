import assert from "node:assert/strict";

/** Opening and navigating a preview must retain the loaded reader underneath it. */
export async function checkPreviewBackground(page, screenshot, search = false) {
  const cards = page.locator(search ? ".search-section-articles .search-result" : ".article-card");
  await cards.first().waitFor();
  const count = await cards.count();
  const card = await cards.first().elementHandle();
  const url = page.url();
  const requests = [];
  const record = (request) => {
    if (
      /\/api\/v1\/(?:feed|user\/(?:feed|bookmarks)|search)$/.test(new URL(request.url()).pathname)
    )
      requests.push(request.url());
  };
  page.on("request", record);
  try {
    await page
      .locator(search ? ".search-result-heading a" : ".card-open-link")
      .first()
      .click();
    await page.locator("#article-preview-title").waitFor();
    assert.equal(await card.evaluate((node) => node.isConnected), true, "preview retains the card");
    assert.equal(await cards.count(), count, "preview retains the complete background list");
    assert.equal(await page.locator('.reader-loading-reveal[data-loading="true"]').count(), 0);
    if (!search && count > 1) {
      const previous = page.url();
      await page.getByRole("button", { name: "Next article", exact: true }).click();
      await page.waitForFunction((value) => location.href !== value, previous);
      await page.locator("#article-preview-title").waitFor();
      assert.equal(await card.evaluate((node) => node.isConnected), true);
    }
    if (screenshot) await page.screenshot({ path: screenshot, animations: "disabled" });
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.locator("dialog.article-modal").waitFor({ state: "detached" });
    assert.equal(page.url(), url);
    assert.equal(await card.evaluate((node) => node.isConnected), true, "closing retains the card");
    assert.equal(requests.length, 0, "preview does not reload the background feed");
  } finally {
    page.off("request", record);
    await card.dispose();
  }
}
