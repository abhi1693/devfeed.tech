import assert from "node:assert/strict";

export const searchSecurityQuery =
  '<img id="search-query-injection" src="data:," onerror="window.__searchInjection=1"> C++ SELECT';
const securityTitle =
  'Code example: <img id="search-title-injection" src="data:," onerror="window.__searchInjection=1">';
const securityDescription =
  'Read <svg id="search-description-injection" onload="window.__searchInjection=1"></svg> and SELECT examples.';
export const searchSecurityEvent = {
  query: searchSecurityQuery,
  result_kind: "articles",
  result_id: "00000000-0000-0000-0000-000000000001",
  click_token: "opaque-fixture-click-token",
};

export function searchFixture(query, sort) {
  if (query === searchSecurityQuery)
    return {
      query,
      sections: {
        articles: {
          items: [
            {
              id: searchSecurityEvent.result_id,
              title: securityTitle,
              description: securityDescription,
              href: "/articles/old",
              image_url: null,
              label: "article",
              published_at: null,
              click_token: searchSecurityEvent.click_token,
            },
          ],
          next_cursor: null,
        },
      },
    };
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

/** Exercise the real shared reader; extensions fulfill the write before it reaches production. */
export async function checkSearchSecurity(page, target, { relayClick = false } = {}) {
  const clicks = [];
  const clickPath = "**/api/v1/search/analytics/click";
  const intercept = async (route) => {
    clicks.push({
      method: route.request().method(),
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON(),
    });
    return relayClick ? route.continue() : route.fulfill({ status: 204 });
  };
  await page.route(clickPath, intercept);
  try {
    await page.goto(target);
    await page
      .getByRole("heading", { name: `Results for “${searchSecurityQuery}”`, exact: true })
      .waitFor();
    const link = page.getByRole("link", { name: securityTitle, exact: true });
    await link.waitFor();
    assert.equal(
      await page.getByText(securityDescription, { exact: true }).innerText(),
      securityDescription,
    );
    assert.equal(
      await page
        .locator("#search-query-injection, #search-title-injection, #search-description-injection")
        .count(),
      0,
      "Search queries and result text must not create HTML elements",
    );
    assert.equal(await page.evaluate(() => window.__searchInjection ?? null), null);
    const accepted = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/search/analytics/click" &&
        response.request().method() === "POST",
    );
    await link.click();
    assert.equal((await accepted).status(), 204);
    assert.deepEqual(clicks, [
      { method: "POST", path: "/api/v1/search/analytics/click", body: searchSecurityEvent },
    ]);
    await page.locator("#article-preview-title").waitFor();
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.locator("dialog.article-modal").waitFor({ state: "detached" });
    await link.waitFor();
  } finally {
    await page.unroute(clickPath, intercept);
  }
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
