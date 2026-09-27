import assert from "node:assert/strict";

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
  page.on("request", capture);
  try {
    await page.goto(target);
    for (let count = 24; count <= 120; count += 24) {
      await page.waitForFunction(
        (expected) => document.querySelectorAll(".article-card .open-count").length >= expected,
        count,
      );
      if (count < 120) await page.locator(".pagination").last().scrollIntoViewIfNeeded();
    }
    assert.equal(await page.locator(".article-card .open-count").count(), 120);
    assert.ok(batches.every((batch) => batch.length > 0 && batch.length <= 100));
    assert.equal(batches.flat().length, 120, "Each loaded article should be requested only once");
    assert.equal(
      new Set(batches.flat()).size,
      120,
      "Previously loaded IDs must not be fetched again",
    );
    console.log(
      `Engagement pagination: 120 unique articles, batch sizes ${batches.map((batch) => batch.length).join(", ")}.`,
    );
  } finally {
    page.off("request", capture);
  }
}
