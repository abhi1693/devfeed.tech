import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const kinds = ["article", "news", "tutorial", "release"];
const title = (kind) => `Default All fixture: ${kind}`;

export function defaultAllFeed(article, params) {
  const selected = params.get("content_type");
  return {
    items: kinds
      .map((kind, index) => ({
        ...article,
        id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
        slug: `default-all-${kind}`,
        title: title(kind),
        content_type: kind,
      }))
      .filter((item) => !selected || item.content_type === selected),
    next_cursor: null,
  };
}

/** The same visible behavior and URL contract must hold in web and packaged readers. */
export async function checkDefaultAll(page, base, output, requests) {
  await mkdir(output, { recursive: true });
  const route = () => {
    const url = new URL(page.url());
    return url.protocol === "chrome-extension:"
      ? new URL(url.hash.slice(1) || "/", "https://devfeed.tech")
      : url;
  };
  const check = async (kind, label) => {
    const expected = kind ? [title(kind)] : kinds.map(title);
    await page.waitForFunction(
      (expected) =>
        JSON.stringify(
          [...document.querySelectorAll(".article-card h2")].map((heading) =>
            heading.textContent.trim(),
          ),
        ) === JSON.stringify(expected),
      expected,
    );
    assert.deepEqual(await page.locator(".article-card h2").allTextContents(), expected, label);
    const selected = page.locator('.feed-toolbar a[aria-current="page"]');
    assert.equal(
      await selected.textContent(),
      kind === "article" ? "Articles" : kind === "tutorial" ? "Tutorials" : "All",
      label,
    );
    assert.equal(route().searchParams.has("content_type"), false, `${label}: clean canonical URL`);
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      `${label}: no horizontal overflow`,
    );
  };
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const entry of ["/", "/latest"]) {
      await page.goto(`${base}${entry}`);
      await check(null, `${width} ${entry}: mixed default`);
      const latest = requests()
        .filter((url) => url.pathname.endsWith("/v1/feed"))
        .at(-1);
      assert.ok(latest, "Default entry requested the public feed");
      assert.equal(
        latest.searchParams.has("content_type"),
        false,
        "Guest defaults never add an Articles API filter",
      );
      await page.reload();
      await check(null, `${width} ${entry}: default reload`);
      await page
        .locator(".feed-toolbar")
        .getByRole("link", { name: "Articles", exact: true })
        .click();
      await page.waitForURL((url) => (url.hash ? url.hash.slice(1) : url.pathname) === "/articles");
      await check("article", `${width} ${entry}: explicit Articles`);
      await page.locator(".feed-toolbar").getByRole("link", { name: "All", exact: true }).click();
      await page.waitForURL((url) => (url.hash ? url.hash.slice(1) : url.pathname) === "/latest");
      await check(null, `${width} ${entry}: switching back to All`);
      await page.goBack();
      await check("article", `${width} ${entry}: Back restores Articles`);
      assert.equal(route().pathname, "/articles");
      await page.goForward();
      await check(null, `${width} ${entry}: Forward restores All`);
      assert.equal(route().pathname, "/latest");
      await page.reload();
      await check(null, `${width} ${entry}: All reload remains mixed`);
    }
    await page.goto(`${base}/tutorials`);
    await check("tutorial", `${width}: explicit typed route`);
    await page.reload();
    await check("tutorial", `${width}: typed route reload`);
    await page.locator(".feed-toolbar").getByRole("link", { name: "All", exact: true }).click();
    await check(null, `${width}: typed route clears back to All`);
    const invitation = page.getByRole("button", { name: "Not now", exact: true });
    if (await invitation.isVisible()) await invitation.click();
    await page.screenshot({
      path: `${output}/default-all-${width === 390 ? "mobile" : "desktop"}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
  const observed = requests().filter((url) => url.pathname.endsWith("/v1/feed"));
  assert.ok(observed.some((url) => !url.searchParams.has("content_type")));
  assert.ok(observed.some((url) => url.searchParams.get("content_type") === "article"));
  assert.ok(observed.some((url) => url.searchParams.get("content_type") === "tutorial"));
  await page.setViewportSize({ width: 1440, height: 1000 });
}
