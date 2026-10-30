import assert from "node:assert/strict";
import { setArticleView } from "../../../../scripts/testing/article-view.mjs";

export function engagementFeed(article, params) {
  const offset = Number(params.get("cursor") ?? 0);
  return {
    items: Array.from({ length: 24 }, (_, index) => {
      const number = offset + index;
      return {
        ...article,
        id: `11111111-1111-4111-8111-${String(number).padStart(12, "0")}`,
        slug: `engagement-${number}`,
        title: `Engagement article ${number}`,
        image_url: null,
      };
    }),
    next_cursor: offset + 24 < 120 ? String(offset + 24) : null,
  };
}

export function engagementRows(params) {
  const ids = params.getAll("article_id");
  assert.ok(ids.length <= 100, `Engagement request exceeds API limit: ${ids.length}`);
  return ids.map((article_id) => ({
    article_id,
    likes: 2,
    opens: 5,
    liked: false,
    bookmarked: false,
  }));
}

export async function checkEngagementPagination(page, target) {
  const batches = [];
  const capture = (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/user/engagement")
      batches.push(url.searchParams.getAll("article_id"));
  };
  async function loadAll(selector) {
    for (let count = 24; count <= 120; count += 24) {
      await page.waitForFunction(
        ({ selector, expected }) => {
          const count = [...document.querySelectorAll(selector)].filter((node) =>
            node.closest(".article-card, .article-list-row")?.checkVisibility(),
          ).length;
          if (count >= expected) return true;
          // Pagination can be replaced as automatic loading finishes. Locate and
          // scroll it in one DOM turn, then wait for the next page's actual rows.
          [...document.querySelectorAll(".pagination")]
            .findLast((node) => node.checkVisibility())
            ?.scrollIntoView({ block: "end", behavior: "instant" });
          return false;
        },
        { selector, expected: count },
      );
    }
  }
  try {
    await page.goto(target);
    await setArticleView(page, "cards");
    await page.locator(".article-card:visible").first().waitFor();
    await page.locator(".article-card:visible .open-count").first().waitFor({ state: "attached" });
    page.on("request", capture);
    await page.reload();
    await loadAll(".article-card .open-count");
    assert.equal(await page.locator(".article-card:visible .open-count").count(), 120);
    assert.ok(batches.every((batch) => batch.length > 0 && batch.length <= 100));
    assert.equal(batches.flat().length, 120, "Each loaded article should be requested only once");
    assert.equal(
      new Set(batches.flat()).size,
      120,
      "Previously loaded IDs must not be fetched again",
    );
    const listBatchStart = batches.length;
    await setArticleView(page, "compact");
    await page.locator(".article-table:visible tbody tr").first().waitFor();
    await loadAll(".article-list-row");
    assert.equal(await page.locator(".article-list-row:visible").count(), 120);
    assert.equal(
      await page.locator(".article-table .open-count, .article-table .heart-button").count(),
      0,
    );
    assert.equal(await page.locator(".article-table:visible .bookmark-button").count(), 120);
    const listIds = batches.slice(listBatchStart).flat();
    assert.equal(
      new Set(listIds).size,
      listIds.length,
      "Returning from Appearance does not repeatedly fetch list engagement",
    );
    await setArticleView(page, "cards");
    await page.locator(".article-card:visible").first().waitFor();
    await loadAll(".article-card .open-count");
    assert.equal(await page.locator(".article-card:visible").count(), 120);
    console.log(
      `Engagement pagination: 120 unique articles, batch sizes ${batches.map((batch) => batch.length).join(", ")}.`,
    );
  } finally {
    page.off("request", capture);
  }
}
