import assert from "node:assert/strict";

export function searchFixture(query, sort) {
  if (query === "infinite-scroll")
    return {
      query,
      sections: {
        articles: {
          items: Array.from({ length: 24 }, (_, index) => ({
            id: String(index),
            title: `Scroll result ${index}`,
            href: `/articles/scroll-${index}`,
            description: "Infinite scroll fixture",
            image_url: null,
            label: "article",
            published_at: null,
          })),
          next_cursor: "2",
        },
      },
    };
  const items = [
    { id: "old", title: "Older matching article", published_at: "2017-09-15T00:00:00Z" },
    { id: "new", title: "Newer matching article", published_at: "2026-09-15T00:00:00Z" },
  ].map((item) => ({
    ...item,
    description: "Search fixture",
    href: `/articles/${item.id}`,
    image_url: null,
    label: "article",
  }));
  return {
    query,
    sections: {
      articles: { items: sort === "newest" ? items.reverse() : items, next_cursor: null },
    },
  };
}

export async function checkSearchFilters(page, target) {
  await page.goto(target);
  const titles = page.locator(".search-result-heading h3");
  await page.getByRole("heading", { name: "Older matching article" }).waitFor();
  assert.equal(await titles.first().innerText(), "Older matching article");
  assert.equal(await page.getByRole("button", { name: "Apply", exact: true }).count(), 0);
  await page.getByRole("combobox", { name: "Article order" }).click();
  await page.getByRole("option", { name: "Newest first" }).click();
  await page.getByText("Updating results…", { exact: true }).waitFor();
  await page.waitForURL(/sort=newest/);
  await page.waitForFunction(
    () =>
      document.querySelector(".search-result-heading h3")?.textContent === "Newer matching article",
  );
  await page.getByRole("combobox", { name: "Results", exact: true }).click();
  await page.getByRole("option", { name: "Articles", exact: true }).click();
  await page.waitForURL(/section=articles/);
  await page.getByRole("heading", { name: "Newer matching article" }).waitFor();
  await page.getByRole("button", { name: "Article date from", exact: true }).click();
  await page.getByRole("combobox", { name: "Year", exact: true }).click();
  await page.getByRole("option", { name: "2020", exact: true }).click();
  await page.getByRole("combobox", { name: "Month", exact: true }).click();
  await page.getByRole("option", { name: "January", exact: true }).click();
  await page.getByRole("button", { name: "Wednesday, January 1, 2020", exact: true }).click();
  await page.waitForURL(/date_from=2020-01-01/);
  await page.getByRole("heading", { name: "Newer matching article" }).waitFor();
  await page.getByRole("link", { name: "Clear filters", exact: true }).click();
  await page.waitForFunction(
    () =>
      document.querySelector(".search-result-heading h3")?.textContent === "Older matching article",
  );
  assert.ok(!page.url().includes("sort="));
}

export async function checkSearchInfiniteScroll(page, target) {
  const requests = [];
  await page.route("**/api/v1/search?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("q") !== "infinite-scroll" || !url.searchParams.has("page"))
      return route.fallback();
    requests.push(url);
    return route.fulfill({
      json: {
        query: "infinite-scroll",
        sections: {
          articles: {
            items: [
              {
                id: "next",
                title: "Automatically appended search article",
                href: "/articles/next",
                description: "Next page",
                image_url: null,
                label: "article",
                published_at: null,
              },
            ],
            next_cursor: null,
          },
        },
      },
    });
  });
  await page.goto(target);
  await page.getByRole("heading", { name: "Scroll result 0", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "More articles", exact: true }).count(), 0);
  assert.equal(await page.getByRole("link", { name: "More articles", exact: true }).count(), 0);
  await page.locator(".search-section-articles .pagination").scrollIntoViewIfNeeded();
  await page.getByRole("heading", { name: "Automatically appended search article" }).waitFor();
  assert.equal(await page.locator(".search-section-articles .search-result").count(), 25);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].searchParams.get("section"), "articles");
  assert.equal(requests[0].searchParams.get("page"), "2");
}
