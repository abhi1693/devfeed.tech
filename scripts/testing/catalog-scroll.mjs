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
  const viewport = page.viewportSize();
  await page.setViewportSize({ width: 800, height: 600 });
  for (const kind of ["topics", "sources"]) {
    const requests = [];
    const capture = (request) => {
      const url = new URL(request.url());
      if (url.pathname === `/api/v1/${kind}`) requests.push(url);
    };
    page.on("request", capture);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.goto(`${origin}/settings/${kind}`);
    const first = page.getByRole("button", { name: `Scroll ${kind} 0`, exact: true });
    await first.waitFor();
    assert.equal(await page.getByRole("button", { name: /^More / }).count(), 0);
    assert.equal(await page.getByRole("link", { name: /^More / }).count(), 0);
    assert.ok(
      requests.every((url) => url.searchParams.get("offset") === "0"),
      `Settings must fetch only the first page before scrolling: ${requests.map(String)}`,
    );
    const search = page.getByRole("searchbox", {
      name: kind === "topics" ? "Find a topic" : "Find a source",
    });
    await first.click();
    await search.fill(`Scroll ${kind} 129`);
    await page.getByRole("button", { name: `Scroll ${kind} 129`, exact: true }).waitFor();
    assert.ok(
      requests.some((url) => url.searchParams.get("q") === `Scroll ${kind} 129`),
      "Search reaches items outside the loaded page",
    );
    await search.fill("");
    await first.waitFor();
    assert.equal(await first.getAttribute("aria-pressed"), "true");
    for (const index of [119, 129]) {
      await page
        .locator("div.pagination[data-has-more]")
        .evaluate((element) => element.scrollIntoView({ block: "end" }));
      await page.getByRole("button", { name: `Scroll ${kind} ${index}`, exact: true }).waitFor();
    }
    assert.equal(await first.getAttribute("aria-pressed"), "true");
    await page
      .getByRole("searchbox", { name: kind === "topics" ? "Find a topic" : "Find a source" })
      .fill(`Scroll ${kind} 129`);
    await page.getByRole("button", { name: `Scroll ${kind} 129`, exact: true }).waitFor();
    await first.waitFor({ state: "detached" });
    await page.getByRole("button", { name: `Scroll ${kind} 129`, exact: true }).waitFor();
    await page.screenshot({ path: `${screenshotPrefix}-${kind}.png`, animations: "disabled" });

    page.off("request", capture);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.goto(`${origin}/${kind}`);
    await page.getByRole("heading", { name: `Scroll ${kind} 0`, exact: true }).waitFor();
    await page.getByRole("button", { name: /^User menu:/ }).waitFor();
    assert.equal(await page.getByRole("button", { name: /^More / }).count(), 0);
    assert.equal(await page.getByRole("link", { name: /^More / }).count(), 0);
    if (kind === "sources")
      await page.locator("button.follow-button:not([disabled])").first().waitFor();
    for (const index of [119, 129]) {
      await page
        .locator("div.pagination[data-has-more]")
        .evaluate((element) => element.scrollIntoView({ block: "end" }));
      await page.getByRole("heading", { name: `Scroll ${kind} ${index}`, exact: true }).waitFor();
    }
    await page.getByText(`You’ve seen all ${kind}.`, { exact: true }).waitFor();
  }
  if (viewport) await page.setViewportSize(viewport);
}
