import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

// Shared geometry checks run in the website and both real unpacked extensions.
export async function checkArticleGrid(page, output) {
  const originalViewport = page.viewportSize();
  await page.locator(".article-card").first().waitFor();
  await mkdir(output, { recursive: true });
  await page.evaluate(() => {
    const grid = document.querySelector(".article-grid");
    const card = grid.querySelector(".article-card");
    for (let index = 0; index < 12; index++) {
      const clone = card.cloneNode(true);
      clone.dataset.gridFixture = "true";
      clone.querySelector("h2 a > span").textContent =
        "A long article title about building readable interfaces ".repeat(5);
      clone.querySelectorAll(".card-tags a").forEach((tag) => {
        tag.textContent = "#AnUnbrokenTopicNameThatMustNeverOverflowTheCard";
      });
      const source = clone.querySelector(".card-source > a, .card-source > span:last-child");
      if (source) source.textContent = "A very long publisher name that needs truncation";
      clone
        .querySelectorAll(".article-engagement .heart-button > span:last-child")
        .forEach((count) => {
          if (!count.querySelector("svg")) count.textContent = "999.9K";
        });
      const opens = clone.querySelector(".open-count");
      if (opens?.lastChild?.nodeType === Node.TEXT_NODE) opens.lastChild.textContent = "999.9K";
      grid.append(clone);
    }
    window.__gridOriginal = {
      sidebar: document.documentElement.getAttribute("data-sidebar-state"),
      dark: document.documentElement.classList.contains("dark"),
    };
  });
  try {
    for (const width of [
      320, 360, 390, 430, 520, 521, 600, 768, 800, 801, 1024, 1200, 1201, 1440, 1920, 2560,
    ]) {
      await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
      for (const sidebar of ["expanded", "collapsed"]) {
        await page.evaluate((state) => {
          document.documentElement.setAttribute("data-sidebar-state", state);
          document.documentElement.classList.toggle("dark", state === "collapsed");
        }, sidebar);
        // Wait for responsive layout and sidebar transitions to settle.
        await page.waitForTimeout(250);
        const result = await page
          .locator(".article-grid")
          .first()
          .evaluate((grid) => {
            const box = grid.getBoundingClientRect();
            const cards = [...grid.querySelectorAll(":scope > .article-card")];
            const visible = (element) =>
              element &&
              getComputedStyle(element).display !== "none" &&
              element.getBoundingClientRect().width > 0;
            return {
              overflow: document.documentElement.scrollWidth > innerWidth,
              columns: getComputedStyle(grid).gridTemplateColumns.split(" ").length,
              expectedColumns: Math.max(1, Math.floor((box.width + 12) / 292)),
              cards: cards.map((card) => {
                const rect = card.getBoundingClientRect();
                const bookmark = card.querySelector(".bookmark-button").getBoundingClientRect();
                const date = card.querySelector(".card-date").getBoundingClientRect();
                const title = card.querySelector("h2").getBoundingClientRect();
                return {
                  width: rect.width,
                  fits: card.scrollWidth <= card.clientWidth,
                  titleWidth: title.width,
                  titleFontSize: Number.parseFloat(
                    getComputedStyle(card.querySelector("h2")).fontSize,
                  ),
                  titleLineHeight: Number.parseFloat(
                    getComputedStyle(card.querySelector("h2")).lineHeight,
                  ),
                  titleHeight: card.querySelector("h2 a > span").getBoundingClientRect().height,
                  bookmarkFits:
                    bookmark.right <= rect.right &&
                    bookmark.left >= rect.left &&
                    bookmark.width >= 44 &&
                    bookmark.height >= 44,
                  dateFits: date.right <= bookmark.left + 1,
                  engagement: visible(card.querySelector(".card-bottom .article-engagement")),
                  narrow: card.clientWidth <= 340,
                };
              }),
            };
          });
        const label = `${width}px, sidebar ${sidebar}`;
        assert.equal(result.overflow, false, `${label}: page overflow`);
        assert.equal(result.columns, result.expectedColumns, `${label}: adaptive columns`);
        for (const card of result.cards) {
          assert.ok(card.width >= Math.min(280, width - 36) - 1, `${label}: readable card width`);
          assert.ok(card.fits && card.bookmarkFits && card.dateFits, `${label}: card footer fits`);
          assert.ok(card.titleWidth >= 230, `${label}: readable title`);
          assert.equal(card.titleFontSize, width <= 520 ? 16 : 14, `${label}: balanced title size`);
          assert.ok(
            card.titleHeight <= card.titleLineHeight * 3 + 1,
            `${label}: title stays within three lines`,
          );
          assert.equal(card.engagement, !card.narrow, `${label}: bookmark-only narrow footer`);
        }
      }
      if ([320, 768, 1440, 2560].includes(width))
        await page.screenshot({ path: `${output}/${width}.png`, fullPage: true });
    }
    // Probe the actual container boundary independently of viewport breakpoints.
    const grid = page.locator(".article-grid").first();
    for (const width of [340, 342, 343, 400]) {
      await grid.evaluate((element, value) => {
        element.style.width = `${value}px`;
      }, width);
      const card = grid.locator(".article-card").first();
      assert.equal(await card.locator(".card-bottom .article-engagement").isVisible(), width > 342);
      assert.equal(await card.locator(".bookmark-button").isVisible(), true);
    }
    console.log(
      `Article grid: 32 screen/sidebar/theme combinations and 4 card-width boundaries passed (${output}).`,
    );
  } finally {
    await page.evaluate(() => {
      document.querySelectorAll('[data-grid-fixture="true"]').forEach((card) => card.remove());
      document.querySelector(".article-grid").style.removeProperty("width");
      const original = window.__gridOriginal;
      if (original.sidebar === null) document.documentElement.removeAttribute("data-sidebar-state");
      else document.documentElement.setAttribute("data-sidebar-state", original.sidebar);
      document.documentElement.classList.toggle("dark", original.dark);
      delete window.__gridOriginal;
    });
    await page.setViewportSize(originalViewport);
  }
}
