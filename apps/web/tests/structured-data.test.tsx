import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import fc from "fast-check";
import { JsonLd } from "@/components/json-ld";
import {
  articleStructuredData,
  collectionStructuredData,
  siteStructuredData,
  pageStructuredData,
} from "@/lib/structured-data";
import {
  articleDescription,
  articleMetadata,
  feedMetadata,
  socialImage,
  SITE_DESCRIPTION,
} from "@/lib/metadata";
import { article as fixtureArticle } from "./fixtures";

const article = {
  ...fixtureArticle,
  publisher: fixtureArticle.sources[0],
  authors: [{ type: "Person" as const, name: fixtureArticle.author! }],
};

afterEach(() => vi.unstubAllEnvs());

it("uses article tags consistently for HTML keywords, page/article JSON-LD and Open Graph", () => {
  const tags = ["python", "json-schema", "openai"];
  const item = { ...article, tags };
  const metadata = articleMetadata(item);
  expect(metadata.keywords).toEqual(tags);
  expect(metadata.openGraph).toHaveProperty("tags", tags);
  const data = articleStructuredData(item);
  expect(data.keywords).toEqual(tags);
  expect(data.mainEntity).toHaveProperty("keywords", tags);
});

it("omits keywords when an article has no tags", () => {
  const item = { ...article, tags: [] };
  expect(articleMetadata(item)).not.toHaveProperty("keywords");
  const data = articleStructuredData(item);
  expect(data).not.toHaveProperty("keywords");
  expect(data.mainEntity).not.toHaveProperty("keywords");
});

it("escapes script termination in publisher strings while preserving valid JSON", () => {
  const title = '</script><script>alert("publisher")</script><img src=x onerror=alert(1)>';
  const data = articleStructuredData({
    ...article,
    title,
    original_title: title,
    ai_summary: title,
    publisher: { ...article.publisher, name: title },
    authors: [{ type: "Organization", name: title }],
  });
  const html = renderToStaticMarkup(<JsonLd data={data} />);
  expect(html.match(/<script/g)).toHaveLength(1);
  expect(html.match(/<\/script>/g)).toHaveLength(1);
  expect(html).not.toContain("<img");
  const json = html.slice(html.indexOf(">") + 1, html.lastIndexOf("</script>"));
  expect(JSON.parse(json)).toEqual(data);
});

it("describes the local preview separately from the original article and credits its publisher", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.test");
  const data = articleStructuredData(article);
  expect(data).toMatchObject({
    "@type": "WebPage",
    url: `https://devfeed.test/articles/${article.slug}`,
    description: article.ai_summary,
    publisher: { "@id": "https://devfeed.test/#organization" },
    hasPart: {
      "@type": "CreativeWork",
      "@id": `https://devfeed.test/articles/${article.slug}#overview`,
      text: article.ai_summary,
      publisher: { "@id": "https://devfeed.test/#organization" },
      isBasedOn: { "@id": article.canonical_url },
    },
    mainEntity: {
      "@type": "Article",
      "@id": article.canonical_url,
      url: article.canonical_url,
      headline: article.title,
      description: article.summary,
      author: [{ "@type": "Person", name: article.author }],
      publisher: {
        "@type": "Organization",
        name: article.sources[0].name,
        url: "https://example.com/",
      },
      datePublished: "2026-09-11T00:00:00.000Z",
    },
  });
  // The branded fallback social card is not the original article's illustration.
  expect(data.mainEntity).not.toHaveProperty("image");
  expect(data.mainEntity).not.toHaveProperty("dateModified");
});

it("omits unknown original dates, authors, publishers and unsafe URLs without inventing data", () => {
  const item = {
    ...article,
    published_at: "invalid",
    author: null,
    authors: [],
    publisher: null,
    sources: [],
    image_url: "javascript:alert(1)",
    canonical_url: "https://user:secret@example.com",
  };
  const data = articleStructuredData(item).mainEntity;
  for (const key of ["datePublished", "dateModified", "author", "publisher", "image", "url", "@id"])
    expect(data).not.toHaveProperty(key);
  const metadata = articleMetadata(item);
  expect(metadata.openGraph).not.toHaveProperty("publishedTime");
  expect(metadata.openGraph).toHaveProperty("images", [socialImage()]);
});

it("keeps the original headline and publisher independent of the local title and discovery order", () => {
  const publisher = article.sources[0];
  const aggregator = { ...publisher, id: "aggregator", name: "A digest", slug: "a-digest" };
  const data = articleStructuredData({
    ...article,
    title: "DevFeed's guide to boundaries",
    sources: [aggregator, publisher],
    authors: [{ type: "Organization", name: "Engineering team", url: "https://example.com/team" }],
  });
  expect(data.name).toBe("DevFeed's guide to boundaries");
  expect(data.mainEntity).toMatchObject({
    headline: article.original_title,
    publisher: { name: publisher.name },
    author: [
      { "@type": "Organization", name: "Engineering team", url: "https://example.com/team" },
    ],
  });
  expect(data.breadcrumb).toMatchObject({
    itemListElement: [
      { position: 1, name: "Home" },
      {
        position: 2,
        name: publisher.name,
        item: expect.stringContaining(`/sources/${publisher.slug}`),
      },
      { position: 3, name: "DevFeed's guide to boundaries" },
    ],
  });
});

it("does not promote legacy discovery metadata or untyped bylines into original attribution", () => {
  const { publisher, authors, original_title, ...legacy } = article;
  void publisher;
  void authors;
  void original_title;
  const data = articleStructuredData(legacy);
  for (const key of ["publisher", "author", "headline"])
    expect(data.mainEntity).not.toHaveProperty(key);
  expect(data.breadcrumb).toHaveProperty("itemListElement.1.name", article.sources[0].name);
});

it.each([null, "", "  \n "])(
  "does not claim a source excerpt is a DevFeed overview (%j)",
  (ai_summary) => {
    const data = articleStructuredData({ ...article, ai_summary });
    expect(data).not.toHaveProperty("hasPart");
    expect(data.description).toBe(article.summary);
  },
);

it("keeps the full English overview separate from the original language, dates and description", () => {
  const ai_summary =
    "## A **useful** overview.\n\n" + "A supported technical takeaway. ".repeat(20);
  const data = articleStructuredData({ ...article, ai_summary, language: "es" });
  expect(data.hasPart).toMatchObject({
    "@type": "CreativeWork",
    name: "AI overview",
    text: ai_summary.trim(),
    encodingFormat: "text/markdown",
    inLanguage: "en",
  });
  expect((data.hasPart as { text: string }).text.length).toBeGreaterThan(180);
  for (const key of [
    "datePublished",
    "dateModified",
    "author",
    "timeRequired",
    "interactionStatistic",
  ])
    expect(data.hasPart).not.toHaveProperty(key);
  expect(data.mainEntity).toMatchObject({ description: article.summary, inLanguage: "es" });
});

it("preserves technical identifiers and Markdown in the overview's declared source format", () => {
  const ai_summary =
    "Use `max_tokens` and `foo_bar` when `x < y`.\n\n" +
    "- Keep escaped \\*punctuation\\* and [provider limits](https://example.com/limits).";
  expect(articleStructuredData({ ...article, ai_summary }).hasPart).toMatchObject({
    text: ai_summary,
    encodingFormat: "text/markdown",
  });
});

it("omits unsafe attribution links and a derivation link without a safe original", () => {
  const data = articleStructuredData({
    ...article,
    canonical_url: "javascript:alert(1)",
    publisher: { ...article.publisher, website_url: "https://user:secret@example.com" },
    authors: [{ type: "Organization", name: "Team", url: "javascript:alert(1)" }],
  });
  expect(data.hasPart).not.toHaveProperty("isBasedOn");
  expect(data.mainEntity).not.toHaveProperty("url");
  expect(data.mainEntity).not.toHaveProperty("publisher.url");
  expect(data.mainEntity).not.toHaveProperty("author.0.url");
});

it("uses the runtime origin and identical local canonical/OG URLs for scoped and paginated feeds", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "http://192.0.2.10:3000");
  const metadata = feedMetadata(
    "Engineering",
    "Developer articles",
    { cursor: "next-page" },
    { source_id: article.sources[0].id, source_slug: "engineering", content_type: "tutorial" },
  );
  const url = "http://192.0.2.10:3000/sources/engineering/tutorials?cursor=next-page";
  expect(metadata.alternates?.canonical).toBe(url);
  expect(metadata.openGraph).toMatchObject({
    url,
    type: "website",
    siteName: "DevFeed",
    images: [socialImage()],
  });
  expect(metadata.twitter).toMatchObject({
    card: "summary_large_image",
    images: [{ url: "http://192.0.2.10:3000/opengraph.png", alt: socialImage().alt }],
  });
  expect(metadata.robots).toEqual({ index: false, follow: true });
});

it("uses the visible article overview and cover for article social cards", () => {
  const item = {
    ...article,
    ai_summary: "## Learn **TypeScript** with [examples](https://example.com).",
    image_url: "https://example.com/cover.png",
  };
  expect(articleDescription(item)).toBe("Learn TypeScript with examples.");
  const metadata = articleMetadata(item);
  expect(metadata.openGraph).toMatchObject({
    type: "article",
    title: `${article.title} · DevFeed`,
    description: "Learn TypeScript with examples.",
    publishedTime: "2026-09-11T00:00:00.000Z",
    images: [{ url: item.image_url, alt: article.title }],
  });
  expect(metadata.twitter).toMatchObject({
    card: "summary_large_image",
    images: [{ url: item.image_url, alt: article.title }],
  });
  expect(articleStructuredData(item).mainEntity).toHaveProperty("image", [item.image_url]);
});

it("normalizes description candidates before choosing a nonempty fallback", () => {
  expect(
    articleDescription({
      ...article,
      ai_summary: "<p></p> ###",
      summary: "**Useful** source text.",
    }),
  ).toBe("Useful source text.");
  expect(articleDescription({ ...article, ai_summary: "  ", summary: "<p></p>" })).toBe(
    article.title,
  );
});

it("ends long descriptions at a word boundary while preserving exact-length descriptions", () => {
  const prefix = "An overview with useful details. ".repeat(5);
  const item = { ...article, ai_summary: `${prefix}Extraordinaryimplementation details.` };
  expect(articleDescription(item)).toBe(prefix.trim());
  expect(articleDescription({ ...article, ai_summary: "a".repeat(180) })).toBe("a".repeat(180));
  expect(articleDescription({ ...article, ai_summary: "a".repeat(181) })).toBe("a".repeat(180));
});

it.each([
  ["[outer [inner](https://example.test) tail", "outer [inner tail"],
  ["[incomplete] then [link](url)", "[incomplete] then link"],
  ["![image](url) [link](url) <b>bold</b>", "image link bold"],
  ["[label](url(with-parentheses))", "label)"],
  ["[broken](url", "[broken](url"],
  ["<broken <tag>visible <unfinished", "visible <unfinished"],
  ["before<>after", "before after"],
  ["", article.summary || article.title],
])("preserves article preview text for %j", (ai_summary, expected) => {
  expect(articleDescription({ ...article, ai_summary })).toBe(expected);
});

it("preserves preview words across generated publisher markup", () => {
  const word = fc
    .array(fc.constantFrom("a", "b", "é", "中", "💻"), { minLength: 1, maxLength: 40 })
    .map((characters) => characters.join(""));
  const token = fc.tuple(word, fc.integer({ min: 0, max: 4 })).map(([text, kind]) => ({
    text,
    markup: [
      text,
      `[${text}](https://example.test)`,
      `![${text}](image.png)`,
      `<b>${text}</b>`,
      `**${text}**`,
    ][kind],
  }));
  fc.assert(
    fc.property(fc.array(token, { minLength: 1, maxLength: 30 }), (tokens) => {
      const ai_summary = tokens.map((item) => item.markup).join("\n");
      const text = tokens.map((item) => item.text).join(" ");
      const description = articleDescription({ ...article, ai_summary });
      if (!/\p{L}/u.test(text)) {
        expect(description).toBe(article.summary);
      } else if (text.length <= 180) {
        expect(description).toBe(text);
      } else {
        expect(description.length).toBeLessThanOrEqual(180);
        expect(description.length).toBeGreaterThan(0);
        expect(text.startsWith(description)).toBe(true);
        // A long single word still needs a bounded preview; otherwise retain whole words.
        expect(text[description.length] === " " || !text.slice(0, 180).includes(" ")).toBe(true);
      }
    }),
    { seed: 20261005, numRuns: 2000 },
  );
});

it.each([
  ["[".repeat(100_000), article.summary],
  ["[a](".repeat(25_000), "[a](".repeat(45)],
  ["<".repeat(100_000), article.summary],
])("bounds cleanup of malformed publisher markup", (ai_summary, expected) => {
  const started = performance.now();
  expect(articleDescription({ ...article, ai_summary })).toBe(expected);
  // The forward scans take milliseconds; allow a full second on slower CI runners.
  expect(performance.now() - started).toBeLessThan(1000);
});

it("falls back from the article cover to its source cover and then DevFeed", () => {
  const sourceImage = "https://example.com/source-cover.png";
  const withSourceCover = articleMetadata({
    ...article,
    sources: [{ ...article.sources[0], image_url: sourceImage }],
  });
  expect(withSourceCover.openGraph).toHaveProperty("images", [
    { url: sourceImage, alt: article.title },
  ]);

  const withoutCovers = articleMetadata(article);
  expect(withoutCovers.openGraph).toHaveProperty("images", [socialImage()]);
});

it("limits collection markup to the rendered items and preserves pagination and breadcrumbs", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.test");
  const data = collectionStructuredData(
    "https://devfeed.test/topics?offset=60",
    "Topics",
    [{ name: "TypeScript", path: "/topics/typescript" }],
    { offset: 60 },
  );
  expect(data).toMatchObject({
    "@type": "CollectionPage",
    url: "https://devfeed.test/topics?offset=60",
    mainEntity: {
      "@type": "ItemList",
      itemListElement: [
        {
          "@type": "ListItem",
          position: 61,
          name: "TypeScript",
          url: "https://devfeed.test/topics/typescript",
        },
      ],
    },
    breadcrumb: {
      itemListElement: [
        { position: 1, item: "https://devfeed.test/" },
        { position: 2, item: "https://devfeed.test/topics?offset=60" },
      ],
    },
  });
  expect(data.mainEntity).not.toHaveProperty("numberOfItems");
  expect(collectionStructuredData("https://devfeed.test/", "Latest feed", [])).not.toHaveProperty(
    "breadcrumb",
  );
  expect(
    collectionStructuredData("https://devfeed.test/", "Latest feed", []).mainEntity,
  ).toHaveProperty("itemListElement", []);
});

it("connects website and organization with absolute stable identifiers and logo URLs", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.test");
  expect(
    siteStructuredData({ src: "/_next/static/media/mark.png", width: 512, height: 512 }),
  ).toMatchObject({
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": "https://devfeed.test/#organization",
        description: SITE_DESCRIPTION,
        logo: {
          "@id": "https://devfeed.test/#logo",
          url: "https://devfeed.test/_next/static/media/mark.png",
          width: 512,
          height: 512,
        },
      },
      {
        "@type": "WebSite",
        "@id": "https://devfeed.test/#website",
        publisher: { "@id": "https://devfeed.test/#organization" },
      },
    ],
  });
});

it("gives public information pages the same canonical identity and valid breadcrumbs as feeds and articles", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.test");
  for (const [path, name] of [
    ["/legal/privacy", "Privacy Policy"],
    ["/mcp", "Connect your agent"],
  ]) {
    const url = `https://devfeed.test${path}`;
    const data = pageStructuredData(url, name, { description: "Visible page introduction." });
    expect(data).toMatchObject({
      "@type": "WebPage",
      "@id": `${url}#webpage`,
      url,
      name,
      description: "Visible page introduction.",
      isPartOf: { "@id": "https://devfeed.test/#website" },
      breadcrumb: {
        "@id": `${url}#breadcrumb`,
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: "https://devfeed.test/" },
          { "@type": "ListItem", position: 2, name, item: url },
        ],
      },
    });
  }
});

it("omits blank optional article identities and preserves only known original publication facts", () => {
  const item = {
    ...article,
    author: "  ",
    authors: [{ type: "Person" as const, name: "  " }],
    publisher: { ...article.publisher, name: "  " },
    sources: [{ ...article.sources[0], name: "  " }],
    published_at: null,
  };
  const entity = articleStructuredData(item).mainEntity;
  for (const key of ["author", "publisher", "datePublished", "dateModified", "image"])
    expect(entity).not.toHaveProperty(key);
  expect(entity).toHaveProperty("@id", article.canonical_url);
  expect(entity).toHaveProperty("description", article.summary);
  expect(entity).not.toHaveProperty("mainEntityOfPage");
});

it("uses a real organization logo that meets Google's minimum image dimensions", () => {
  const png = readFileSync(
    new URL("../../../packages/theme/assets/devfeed-mark.png", import.meta.url),
  );
  expect(png.subarray(1, 4).toString()).toBe("PNG");
  expect(png.readUInt32BE(16)).toBeGreaterThanOrEqual(112);
  expect(png.readUInt32BE(20)).toBeGreaterThanOrEqual(112);
});

it("serves an actual PNG matching the declared social image size and Twitter's size limit", () => {
  const png = readFileSync(new URL("../public/opengraph.png", import.meta.url));
  expect(png.subarray(1, 4).toString()).toBe("PNG");
  expect(png.readUInt32BE(16)).toBe(socialImage().width);
  expect(png.readUInt32BE(20)).toBe(socialImage().height);
  expect(png.length).toBeLessThan(5 * 1024 * 1024);
});

it("distinguishes type subfeeds from their topic, source and tag landing pages", () => {
  for (const scope of [
    { topic: "typescript" },
    { tag: "typescript" },
    { source_id: article.sources[0].id, source_slug: "engineering" },
  ]) {
    const landing = feedMetadata("TypeScript", "Typed JavaScript.", {}, scope);
    const tutorials = feedMetadata(
      "TypeScript",
      "Typed JavaScript.",
      {},
      { ...scope, content_type: "tutorial" },
    );
    expect(landing.title).toBe("TypeScript");
    expect(tutorials.title).toBe("TypeScript tutorials");
    expect(tutorials.openGraph).toHaveProperty("title", "TypeScript tutorials · DevFeed");
  }
});
