import assert from "node:assert/strict";

export function snippetArticle(article) {
  return {
    ...article,
    slug: "snippet-article",
    title: "Android Studio Rabbit 1 now available",
    ai_summary: "Android Studio Rabbit 1 is available in the Stable channel.",
    summary: "Install this Android Studio stable release.",
  };
}

/** Keep the main article available for snippets and related copy out of them. */
export function checkSnippetDocument(document, background = false) {
  for (const selector of ["#article-preview-title", ".preview-summary"]) {
    const content = document.querySelector(selector);
    assert.ok(content, `${selector} is present`);
    assert.equal(content.closest("[data-nosnippet]"), null);
  }
  const sidebar = document.querySelector(".preview-sidebar");
  assert.equal(sidebar.tagName, "DIV");
  assert.ok(sidebar.hasAttribute("data-nosnippet"));
  if (background) {
    const cards = document.querySelectorAll(".article-card");
    assert.ok(cards.length > 0, "Background articles remain crawlable");
    for (const card of cards) assert.ok(card.closest("[data-nosnippet]"));
    const title = document.querySelector("title").textContent;
    assert.equal(title, "Android Studio Rabbit 1 now available · DevFeed");
    const description = document.querySelector('meta[name="description"]').content;
    assert.equal(description, snippetArticle({}).ai_summary);
    for (const selector of ['meta[property="og:description"]', 'meta[name="twitter:description"]'])
      assert.equal(document.querySelector(selector).content, description);
    const graphs = [...document.querySelectorAll('script[type="application/ld+json"]')].map(
      (script) => JSON.parse(script.textContent),
    );
    const pages = graphs.filter((graph) => graph["@type"] === "WebPage");
    assert.equal(pages.length, 1);
    assert.equal(pages[0].description, description);
    assert.equal(pages[0].mainEntity.headline, snippetArticle({}).title);
    assert.equal(pages[0].url, document.querySelector('link[rel="canonical"]').href);
    assert.equal(
      graphs.some((graph) => graph["@type"] === "CollectionPage"),
      false,
    );
  }
}

export async function checkArticleSnippets(page, href) {
  await page.goto(href);
  await page.locator("#article-preview-title").waitFor();
  assert.equal(
    await page.locator("#article-preview-title").textContent(),
    snippetArticle({}).title,
  );
  for (const selector of ["#article-preview-title", ".preview-summary"])
    assert.equal(
      await page.locator(selector).evaluate((node) => !!node.closest("[data-nosnippet]")),
      false,
    );
  assert.equal(await page.locator(".preview-sidebar").getAttribute("data-nosnippet"), "true");
  await page.getByRole("button", { name: "Close preview", exact: true }).click();
  await page.locator("dialog.article-modal[open]").waitFor({ state: "hidden" });
  await page.locator(".article-card").first().waitFor();
  assert.equal(await page.locator("dialog.article-modal[open]").count(), 0);
}
