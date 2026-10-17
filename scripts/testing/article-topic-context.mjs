import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const apiPath = (url) => url.pathname.replace(/^\/api(?=\/v1\/)/, "");

/** Distinct meanings of iteration must retain their own approved topic context. */
export function articleTopicContextFixture(article) {
  const design = {
    id: "55555555-5555-4555-8555-555555555551",
    slug: "design-iteration",
    name: "Design iteration",
    kind: "concept",
    description: "Human-centered design develops a solution through prototypes and feedback.",
    ai_description: null,
    logo_url: null,
    website_url: null,
  };
  const loops = {
    ...design,
    id: "55555555-5555-4555-8555-555555555552",
    slug: "programming-iteration",
    name: "Iteration",
    description: "Programming loops repeat instructions using for, foreach, do, and while.",
  };
  const cases = [
    {
      slug: "topic-context-design",
      title: "Turning ambiguity into opportunity through human-centered design",
      ai_summary:
        "Designers test feasibility and iterate on prototypes to meet business objectives.",
      tags: ["iteration"],
      topic: design,
    },
    {
      slug: "topic-context-unassigned",
      title: "Exploring an uncertain design problem without an approved design topic",
      ai_summary: "A design team iterates on prototypes and gathers feedback from people.",
      tags: ["iteration"],
      topic: null,
    },
    {
      slug: "topic-context-loops",
      title: "Programming iteration with for and while loops",
      ai_summary: "Learn how for, foreach, do, and while loops repeat program instructions.",
      tags: ["iteration"],
      topic: loops,
    },
  ].map(({ topic, ...item }, index) => ({
    topic,
    article: {
      ...article,
      ...item,
      id: `22222222-2222-4222-8222-${String(index + 1).padStart(12, "0")}`,
      topics: topic ? [{ ...topic, role: "primary" }] : [],
    },
  }));
  return {
    cases,
    handles(url) {
      const path = apiPath(url);
      return (
        cases.some(({ article }) => path === `/v1/articles/${article.slug}`) ||
        [design, loops].some((topic) => path === `/v1/topics/${topic.slug}`)
      );
    },
    respond(url) {
      const path = apiPath(url);
      const found = cases.find(({ article }) => path === `/v1/articles/${article.slug}`);
      if (found)
        return {
          json: url.pathname.startsWith("/api/") ? found : found.article,
        };
      return { json: [design, loops].find((topic) => path === `/v1/topics/${topic.slug}`) };
    },
  };
}

/** Topic assignments are also correct in the server document before hydration. */
export function checkArticleTopicDocument(document, { article, topic }) {
  assert.equal(document.querySelector("#article-preview-title").textContent, article.title);
  // Suspense may stream the topic outside its sidebar before hydration moves it into place.
  const briefs = document.querySelectorAll(".topic-brief:not(.source-brief)");
  assert.equal(briefs.length, topic ? 1 : 0);
  if (topic) {
    assert.equal(briefs[0].getAttribute("aria-label"), `About ${topic.name}`);
    assert.equal(
      briefs[0].querySelector(".topic-brief-description").textContent,
      topic.description,
    );
  }
}

/** Corrected assignments render consistently without hiding valid programming topics. */
export async function checkArticleTopicContext(page, destination, fixture, output) {
  await mkdir(output, { recursive: true });
  // Visit the unsupported case after a valid topic to catch a stale topic sidebar.
  for (const index of [2, 1, 0]) {
    const { article, topic } = fixture.cases[index];
    await page.goto(destination(`/articles/${article.slug}`));
    const title = page.locator("#article-preview-title");
    await title.waitFor();
    assert.equal(await title.textContent(), article.title);
    const brief = page.locator(".preview-sidebar .topic-brief").filter({
      has: page.getByText("About this topic", { exact: true }),
    });
    if (topic) {
      await brief.getByText(topic.description, { exact: true }).waitFor();
      assert.equal(await brief.count(), 1);
      assert.equal(await brief.getAttribute("aria-label"), `About ${topic.name}`);
      assert.equal(await brief.getByRole("link", { name: topic.name, exact: true }).count(), 1);
    } else {
      assert.equal(await brief.count(), 0, "an unmatched design article has no topic panel");
      assert.equal(await page.locator(".preview-topics").textContent(), "#iteration");
    }
    if (index !== 2)
      assert.equal(
        await page
          .locator(".preview-sidebar")
          .getByText(fixture.cases[2].topic.description)
          .count(),
        0,
        "design copy does not inherit the programming-loop definition",
      );
    await page.screenshot({ path: `${output}/${article.slug}.png`, animations: "disabled" });
  }
}
