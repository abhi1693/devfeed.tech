import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { JsonLd } from "@/components/json-ld";
import {
  articleStructuredData,
  collectionStructuredData,
  siteStructuredData,
} from "@/lib/structured-data";
import { articleDescription, articleMetadata, feedMetadata, socialImage } from "@/lib/metadata";
import { article } from "./fixtures";

afterEach(() => vi.unstubAllEnvs());

it("escapes script termination in publisher strings while preserving valid JSON", () => {
  const title = '</script><script>alert("publisher")</script><img src=x onerror=alert(1)>';
  const data = articleStructuredData({ ...article, title });
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
    mainEntity: {
      "@type": "Article",
      "@id": article.canonical_url,
      url: article.canonical_url,
      headline: article.title,
      author: { "@type": "Person", name: article.author },
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

it("uses the runtime origin and identical local canonical/OG URLs for scoped and paginated feeds", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "http://192.168.1.101:3000");
  const metadata = feedMetadata(
    "Engineering",
    "Developer articles",
    { cursor: "next-page" },
    { source_id: article.sources[0].id, source_slug: "engineering", content_type: "tutorial" },
  );
  const url = "http://192.168.1.101:3000/sources/engineering/tutorials?cursor=next-page";
  expect(metadata.alternates?.canonical).toBe(url);
  expect(metadata.openGraph).toMatchObject({
    url,
    type: "website",
    siteName: "DevFeed",
    images: [socialImage()],
  });
  expect(metadata.twitter).toMatchObject({
    card: "summary_large_image",
    images: [{ url: "http://192.168.1.101:3000/opengraph.png", alt: socialImage().alt }],
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
  expect(siteStructuredData("/_next/static/media/mark.png")).toMatchObject({
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": "https://devfeed.test/#organization",
        logo: { url: "https://devfeed.test/_next/static/media/mark.png" },
      },
      {
        "@type": "WebSite",
        "@id": "https://devfeed.test/#website",
        publisher: { "@id": "https://devfeed.test/#organization" },
      },
    ],
  });
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
