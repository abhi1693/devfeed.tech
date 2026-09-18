import assert from "node:assert/strict";

export function catalogChoices(kind) {
  return Array.from({ length: 130 }, (_, index) => ({
    id: `scroll-${kind}-${index}`,
    name: `Scroll ${kind} ${index}`,
    slug: `scroll-${kind}-${index}`,
    ...(kind === "topics" ? { kind: "technology" } : { website_url: "https://example.test" }),
    logo_url: null,
    description: null,
    ai_description: null,
  }));
}

export async function checkCatalogScroll(page, origin, screenshotPrefix) {
  await page.bringToFront();
  for (const kind of ["topics", "sources"]) {
    await page.goto(`${origin}/settings/${kind}`);
    const first = page.getByRole("button", { name: `Scroll ${kind} 0`, exact: true });
    await first.waitFor();
    assert.equal(await page.getByRole("button", { name: /^More / }).count(), 0);
    assert.equal(await page.getByRole("link", { name: /^More / }).count(), 0);
    await first.click();
    for (const index of [119, 129]) {
      await page.locator("div.pagination[data-has-more]").scrollIntoViewIfNeeded();
      await page.getByRole("button", { name: `Scroll ${kind} ${index}`, exact: true }).waitFor();
    }
    assert.equal(await first.getAttribute("aria-pressed"), "true");
    await page
      .getByRole("searchbox", { name: kind === "topics" ? "Find a topic" : "Find a source" })
      .fill(`Scroll ${kind} 129`);
    await page.getByRole("button", { name: `Scroll ${kind} 129`, exact: true }).waitFor();
    assert.equal(await first.count(), 0);
    await page.screenshot({ path: `${screenshotPrefix}-${kind}.png`, animations: "disabled" });

    await page.goto(`${origin}/${kind}`);
    await page.getByRole("heading", { name: `Scroll ${kind} 0`, exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: /^More / }).count(), 0);
    assert.equal(await page.getByRole("link", { name: /^More / }).count(), 0);
    for (const index of [119, 129]) {
      await page.locator("div.pagination[data-has-more]").scrollIntoViewIfNeeded();
      await page.getByRole("heading", { name: `Scroll ${kind} ${index}`, exact: true }).waitFor();
    }
    await page.getByText(`You’ve seen all ${kind}.`, { exact: true }).waitFor();
  }
}
