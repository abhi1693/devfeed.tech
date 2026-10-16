import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { withManagedSourceLogo } from "./source-logos.mjs";
import { catalogChoices } from "./catalog-scroll.mjs";

export function readerDomCatalogFixture() {
  const catalogs = Object.fromEntries(
    ["topics", "sources"].map((kind) => {
      const items = catalogChoices(kind);
      for (const item of items) if (item.logo_url) withManagedSourceLogo(item);
      items[7].name = "Long".repeat(30);
      return [kind, items];
    }),
  );
  return {
    active: false,
    catalogs,
    handles(url) {
      return this.active && /^\/(?:api\/)?v1\/(topics|sources)$/.test(url.pathname);
    },
    respond(url) {
      const items = catalogs[url.pathname.split("/").at(-1)];
      const offset = Number(url.searchParams.get("cursor") ?? url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 60);
      const page = items.slice(offset, offset + limit);
      return url.pathname.startsWith("/api/")
        ? {
            items: page,
            next_cursor: offset + limit < items.length ? String(offset + limit) : null,
          }
        : page;
    },
  };
}

/** Run against the web reader and each actual built extension. */
export async function checkReaderDom(page, destination, fixture, output) {
  await mkdir(dirname(output), { recursive: true });
  await page.context().addInitScript(() => {
    if (location.protocol !== "about:") localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  await page.bringToFront();
  fixture.active = true;
  try {
    for (const width of [1350, 412]) {
      await page.setViewportSize({ width, height: 823 });
      await page.goto("about:blank");
      await page.goto(destination("/latest"));
      const card = page.locator(".article-card").first();
      await card.locator(".open-count").waitFor({ state: "attached" });
      const count = await card.evaluate(
        (node) =>
          node.querySelectorAll("*").length +
          1 -
          (node.querySelector(".image-placeholder")?.querySelectorAll("*").length ?? 0),
      );
      assert.ok(count <= 38, `Feed card element budget: ${count}`);
      await card.locator("h2 a[href]").waitFor();
      await card.locator("time.card-date[datetime]").waitFor();
      for (const kind of ["topics", "sources"]) {
        await page.goto(destination(`/${kind}`));
        const cards = page.locator(".catalog-card");
        await page.waitForFunction(() => document.querySelectorAll(".catalog-card").length === 60);
        const first = cards.first();
        await first.locator("a.follow-button").waitFor();
        const count = await first.evaluate(
          (node) =>
            node.querySelectorAll("*").length +
            1 -
            node.querySelector(".topic-icon").querySelectorAll("*").length,
        );
        assert.ok(count <= 9, `Catalog card element budget: ${count}`);
        for (const expected of [120, 130]) {
          await page
            .locator('div.pagination[data-has-more="true"]')
            .evaluate((node) => node.scrollIntoView({ block: "end" }));
          await page.waitForFunction(
            (expected) => document.querySelectorAll(".catalog-card").length === expected,
            expected,
          );
        }
        assert.deepEqual(
          await cards.locator("h2").allTextContents(),
          fixture.catalogs[kind].map(({ name }) => name),
        );
        await page.getByText(`You’ve seen all ${kind}.`, { exact: true }).waitFor();
        const last = cards.last().locator("h2").locator("..");
        assert.ok((await last.getAttribute("href")).endsWith(`/${kind}/scroll-${kind}-129`));
        await last.focus();
        await page.keyboard.press("Tab");
        assert.equal(
          await cards
            .last()
            .locator(".follow-button")
            .evaluate((node) => node === document.activeElement),
          true,
        );
        await page.keyboard.press("Shift+Tab");
        assert.equal(await last.evaluate((node) => node === document.activeElement), true);
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          true,
          "Long headings must wrap without horizontal overflow",
        );
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: `${output}-${width}-${kind}.png`, animations: "disabled" });
      }
    }
  } finally {
    fixture.active = false;
  }
}
