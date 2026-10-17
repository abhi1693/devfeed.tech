import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

export const tagFixtures = [
  { id: "55555555-5555-4555-8555-000000000001", name: ".NET", slug: ".net" },
  { id: "55555555-5555-4555-8555-000000000002", name: "Artificial intelligence", slug: "ai" },
  { id: "55555555-5555-4555-8555-000000000003", name: "C++", slug: "c++" },
  { id: "55555555-5555-4555-8555-000000000004", name: "CSS", slug: "css" },
  { id: "55555555-5555-4555-8555-000000000005", name: "Rust", slug: "rust" },
  { id: "55555555-5555-4555-8555-000000000006", name: "数据库", slug: "数据库" },
];
export function tagsFixture(url) {
  const path = url.pathname.replace(/^\/api/, "");
  if (path === "/v1/tags") {
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const letter = url.searchParams.get("letter");
    const candidates =
      query === "scroll"
        ? Array.from({ length: 125 }, (_, i) => ({
            id: `scroll-${i}`,
            name: `Scroll tag ${String(i).padStart(3, "0")}`,
            slug: `scroll-${i}`,
          }))
        : tagFixtures;
    const matching = candidates.filter((tag) => {
      const first = tag.name[0].toUpperCase();
      return (
        tag.name.toLowerCase().includes(query) &&
        (!letter || (letter === "#" ? !/^[A-Z]$/.test(first) : first === letter))
      );
    });
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 60);
    const items = matching.slice(offset, offset + limit);
    return {
      json: url.pathname.startsWith("/api")
        ? {
            items,
            next_cursor:
              offset + items.length < matching.length ? String(offset + items.length) : null,
          }
        : items,
    };
  }
  if (path.startsWith("/v1/tags/")) {
    const item = tagFixtures.find((tag) => tag.slug === decodeURIComponent(path.slice(9)));
    return { status: item ? 200 : 404, json: item ?? { detail: "Not found" } };
  }
}
export async function checkTags(page, pathFor, output) {
  try {
    return await runTags(page, pathFor, output);
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png` });
    console.error(page.url(), await page.locator("body").innerText());
    throw error;
  }
}
async function runTags(page, pathFor, output) {
  await mkdir(output, { recursive: true });
  await page.goto(pathFor("/tags"));
  await page.getByRole("heading", { name: "Explore tags", exact: true }).waitFor();
  await page.getByRole("link", { name: "Artificial intelligence", exact: true }).waitFor();
  await page.screenshot({ path: `${output}/tags-desktop.png`, animations: "disabled" });
  await page
    .getByRole("navigation", { name: "Filter tags by letter" })
    .getByRole("link", { name: "C", exact: true })
    .click();
  await page
    .getByRole("link", { name: "C", exact: true })
    .and(page.locator("[aria-current=page]"))
    .waitFor();
  assert.equal(await page.locator(".tags-group li").count(), 2);
  await page.getByRole("link", { name: "C++", exact: true }).click();
  await page
    .locator(".topic-feed-heading")
    .getByRole("heading", { name: "C++", exact: true })
    .waitFor();
  await page.locator(".article-card").first().waitFor();
  await page.locator(".feed-header").getByRole("link", { name: "Articles", exact: true }).click();
  await page.waitForURL((url) =>
    (url.hash ? url.hash.slice(1) : url.pathname).endsWith("/tags/c%2B%2B/articles"),
  );
  await page
    .locator(".topic-feed-heading")
    .getByRole("heading", { name: "C++", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Share tag: C++", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "Share tag", exact: true });
  await panel.waitFor();
  for (const link of await panel.getByRole("link").all())
    assert.equal(
      new URL(new URL(await link.getAttribute("href")).searchParams.get("url")).pathname,
      "/tags/c%2B%2B",
    );
  await page.keyboard.press("Escape");
  await page.screenshot({ path: `${output}/tag-desktop.png`, animations: "disabled" });
  await page.getByRole("link", { name: "Explore tags", exact: true }).click();
  await page.getByRole("textbox", { name: "Search all tags" }).fill("rust");
  await page.locator(".tags-search").getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("link", { name: "Rust", exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".tags-group li").length === 1);
  await page.getByRole("textbox", { name: "Search all tags" }).fill("no-such-tag");
  await page.locator(".tags-search").getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("heading", { name: "No tags found", exact: true }).waitFor();
  await page.getByRole("link", { name: "Browse all tags", exact: true }).click();
  await page.getByRole("link", { name: "Artificial intelligence", exact: true }).waitFor();
  assert.equal(await page.getByRole("textbox", { name: "Search all tags" }).inputValue(), "");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("heading", { name: "Explore tags", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: `${output}/tags-mobile.png`, animations: "disabled" });
  await page
    .getByRole("navigation", { name: "Filter tags by letter" })
    .getByRole("link", { name: "#", exact: true })
    .click();
  await page.getByRole("link", { name: ".NET", exact: true }).waitFor();
  await page.getByRole("link", { name: "数据库", exact: true }).click();
  await page
    .locator(".topic-feed-heading")
    .getByRole("heading", { name: "数据库", exact: true })
    .waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: `${output}/tag-mobile.png`, animations: "disabled" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(pathFor("/tags"));
  await page.getByRole("heading", { name: "Explore tags", exact: true }).waitFor();
  await page.screenshot({ path: `${output}/tags-dark.png`, animations: "disabled" });
  let failedNextPage = false;
  await page.route("**/api/v1/tags?*", async (route) => {
    const url = new URL(route.request().url());
    if (
      !failedNextPage &&
      url.searchParams.get("q") === "scroll" &&
      url.searchParams.get("offset") === "60"
    ) {
      failedNextPage = true;
      return route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } });
    }
    return route.fallback();
  });
  await page.goto(pathFor("/tags?q=scroll&letter=S"));
  await page.getByRole("link", { name: "Scroll tag 059", exact: true }).waitFor();
  await page.locator(".tags-directory .pagination").scrollIntoViewIfNeeded();
  await page.getByText("Couldn’t load more tags.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  for (const last of ["Scroll tag 119", "Scroll tag 124"]) {
    await page.locator(".tags-directory .pagination").scrollIntoViewIfNeeded();
    await page.getByRole("link", { name: last, exact: true }).waitFor();
  }
  assert.equal(await page.locator(".tags-group li").count(), 125);
  assert.equal(await page.getByRole("link", { name: /^(Next|Previous) tags$/ }).count(), 0);
  await page.getByText("You’ve seen all matching tags.", { exact: true }).waitFor();
  await page.getByRole("textbox", { name: "Search all tags" }).fill("no-such-tag");
  await page.locator(".tags-search").getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("heading", { name: "No tags found", exact: true }).waitFor();
  assert.equal(await page.locator(".tags-group li").count(), 0);
}

export async function checkTagFollow(page, pathFor, output) {
  await page.goto(pathFor("/tags/c%2B%2B"));
  const heading = page.locator(".topic-feed-heading");
  await heading.getByRole("heading", { name: "C++", exact: true }).waitFor();
  await heading.getByRole("button", { name: "Follow", exact: true }).waitFor();
  await heading.getByRole("button", { name: "Follow", exact: true }).click();
  await heading.getByText("Couldn’t update this tag. Please try again.", { exact: true }).waitFor();
  await heading.getByRole("button", { name: "Follow", exact: true }).click();
  await heading.getByRole("button", { name: "Following", exact: true }).waitFor();
  await page.reload();
  await heading.getByRole("button", { name: "Following", exact: true }).waitFor();
  await heading.getByRole("button", { name: "Following", exact: true }).click();
  await heading.getByRole("button", { name: "Follow", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: /^Remove tag filter/ }).count(), 0);
  await page.screenshot({ path: `${output}/tag-follow.png`, animations: "disabled" });
}
