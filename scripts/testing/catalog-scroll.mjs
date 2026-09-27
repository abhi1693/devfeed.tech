import assert from "node:assert/strict";
import sharp from "sharp";

export function catalogChoices(kind) {
  return Array.from({ length: 130 }, (_, index) => ({
    id: `scroll-${kind}-${index}`,
    name: `Scroll ${kind} ${index}`,
    slug: `scroll-${kind}-${index}`,
    ...(kind === "topics" ? { kind: "technology" } : { website_url: "https://example.test" }),
    logo_url:
      kind === "topics" ? "https://logos.example.test/topic-logos/v1/catalog/64.webp" : null,
    logo_variants:
      kind === "topics"
        ? [32, 64, 96].map((width) => ({
            url: `https://logos.example.test/topic-logos/v1/catalog/${width}.webp`,
            width,
          }))
        : [],
    description: null,
    ai_description: null,
  }));
}

export async function checkCatalogScroll(page, origin, screenshotPrefix) {
  await page.bringToFront();
  await page.route("https://logos.example.test/topic-logos/v1/catalog/*", async (route) => {
    const width = Number(new URL(route.request().url()).pathname.split("/").at(-1).split(".")[0]);
    const body = await sharp({
      create: { width, height: width, channels: 4, background: { r: 0, g: 120, b: 220, alpha: 1 } },
    })
      .webp()
      .toBuffer();
    await route.fulfill({
      contentType: "image/webp",
      body,
      headers: { "Access-Control-Allow-Origin": "*" },
    });
  });
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
    if (kind === "topics") {
      await first.locator("img").waitFor();
      await page.waitForFunction(() => {
        const image = document.querySelector(".topic-logo img");
        return image?.complete && image.naturalWidth > 0;
      });
      const image = first.locator("img");
      assert.equal(await image.getAttribute("sizes"), "30px");
      assert.match(await image.evaluate((node) => node.currentSrc), /\/(32|64)\.webp$/);
    }
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
