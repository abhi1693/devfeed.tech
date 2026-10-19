import assert from "node:assert/strict";

export function snippetArticle(article) {
  const publisher = {
    id: "68686868-6868-4868-8868-686868686868",
    slug: "rabbit-engineering",
    name: "Rabbit Engineering",
    website_url: "https://publisher.example.test/",
    logo_url: null,
    description: null,
  };
  return {
    ...article,
    slug: "snippet-article",
    title: "Android Studio Rabbit 1 now available",
    original_title: "Android Studio Rabbit 1 stable release",
    canonical_url: "https://publisher.example.test/rabbit-1",
    publisher,
    author: "Rabbit team",
    authors: [{ type: "Organization", name: "Rabbit team" }],
    sources: [
      { ...publisher, id: "curator", slug: "a-digest", name: "A developer digest" },
      publisher,
    ],
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
    assert.equal(pages[0].mainEntity.headline, snippetArticle({}).original_title);
    assert.equal(pages[0].mainEntity.publisher.name, snippetArticle({}).publisher.name);
    const organization = graphs
      .find((graph) => graph["@graph"])
      ["@graph"].find((entity) => entity["@type"] === "Organization");
    assert.equal(pages[0].publisher["@id"], organization["@id"]);
    assert.equal(pages[0].hasPart.text, snippetArticle({}).ai_summary);
    assert.equal(pages[0].hasPart.inLanguage, "en");
    assert.equal(pages[0].hasPart.isBasedOn["@id"], snippetArticle({}).canonical_url);
    assert.deepEqual(
      pages[0].breadcrumb.itemListElement.map((item) => item.name),
      ["Home", snippetArticle({}).publisher.name, snippetArticle({}).title],
    );
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
  const { publisher, original_title, ai_summary } = snippetArticle({});
  assert.equal(await page.locator(".preview-publisher a").first().textContent(), publisher.name);
  assert.ok(
    (await page.locator(".preview-publisher a").first().getAttribute("href")).includes(
      publisher.slug,
    ),
  );
  assert.equal(await page.locator(".source-brief h2").textContent(), publisher.name);
  assert.equal(await page.locator("#article-overview h2").textContent(), "AI overview");
  assert.equal(await page.locator("#article-overview p").textContent(), ai_summary);
  assert.equal(await page.getByText("A developer digest", { exact: true }).count(), 0);
  assert.equal(await page.getByRole("heading", { name: original_title, exact: true }).count(), 0);
  await page.getByRole("button", { name: "Close preview", exact: true }).click();
  await page.locator("dialog.article-modal[open]").waitFor({ state: "hidden" });
  await page.locator(".article-card").first().waitFor();
  assert.equal(await page.locator("dialog.article-modal[open]").count(), 0);
}
