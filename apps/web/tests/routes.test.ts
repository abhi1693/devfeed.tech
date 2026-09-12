import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Home, { generateMetadata as homeMetadata } from "@/app/page";
import { generateMetadata as tagMetadata } from "@/app/tags/[slug]/page";
import { generateMetadata as topicsMetadata } from "@/app/topics/page";
import { generateMetadata as sourcesMetadata } from "@/app/sources/page";
import ContentFeed, { generateMetadata as contentMetadata } from "@/app/[contentType]/page";
import Articles from "@/app/articles/page";
import TopicPage, { generateMetadata as topicMetadata } from "@/app/topics/[slug]/page";
import SourcePage, { generateMetadata as sourceMetadata } from "@/app/sources/[id]/page";
import { generateMetadata as articleMetadata } from "@/app/articles/[slug]/page";
import { FeedView } from "@/components/feed-view";
import * as api from "@/lib/api";
import { article, source, topic } from "./fixtures";
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NOT_FOUND");
  },
  redirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
  permanentRedirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));
vi.mock("@/components/feed-view", () => ({
  FeedView: vi.fn().mockResolvedValue(null),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getTopic: vi.fn(),
  getSource: vi.fn(),
  getArticle: vi.fn(),
  getTag: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTopic).mockResolvedValue(topic);
  vi.mocked(api.getSource).mockResolvedValue(source);
  vi.mocked(api.getArticle).mockResolvedValue(article);
  vi.mocked(api.getTag).mockResolvedValue({ id: "tag", name: "C++", slug: "c++" });
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
});
afterEach(() => vi.unstubAllEnvs());

it("gives public pages absolute canonicals matching their sitemap URLs", async () => {
  const searchParams = Promise.resolve({ utm_source: "newsletter", fbclid: "tracking" });
  expect(await homeMetadata({ searchParams })).toMatchObject({
    alternates: { canonical: "https://devfeed.tech/" },
  });
  expect((await homeMetadata({ searchParams })).robots).toBeUndefined();
  expect(
    await topicMetadata({ params: Promise.resolve({ slug: topic.slug }), searchParams }),
  ).toMatchObject({ alternates: { canonical: `https://devfeed.tech/topics/${topic.slug}` } });
  expect(
    await sourceMetadata({ params: Promise.resolve({ id: source.slug }), searchParams }),
  ).toMatchObject({ alternates: { canonical: `https://devfeed.tech/sources/${source.slug}` } });
  expect(
    await tagMetadata({ params: Promise.resolve({ slug: "c++" }), searchParams }),
  ).toMatchObject({ alternates: { canonical: "https://devfeed.tech/tags/c%2B%2B" } });
  const metadata = await articleMetadata({ params: Promise.resolve({ slug: article.slug }) });
  expect(metadata.alternates?.canonical).toBe(`https://devfeed.tech/articles/${article.slug}`);
  expect(metadata.alternates?.canonical).not.toBe(article.canonical_url);
});

it("preserves feed pagination and meaningful filters in typed canonical URLs", async () => {
  const searchParams = Promise.resolve({
    language: "en",
    cursor: "opaque+/=",
    utm_source: "email",
    content_type: "news",
  });
  const metadata = await topicMetadata({
    params: Promise.resolve({ slug: topic.slug, contentType: "tutorials" }),
    searchParams,
  });
  expect(metadata.alternates?.canonical).toBe(
    `https://devfeed.tech/topics/${topic.slug}/tutorials?language=en&cursor=opaque%2B%2F%3D`,
  );
  expect(metadata.robots).toEqual({ index: false, follow: true });
  expect(
    (
      await contentMetadata({
        params: Promise.resolve({ contentType: "news" }),
        searchParams: Promise.resolve({ utm_source: "email" }),
      })
    ).alternates?.canonical,
  ).toBe("https://devfeed.tech/news");
  expect(
    (
      await tagMetadata({
        params: Promise.resolve({ slug: "c++" }),
        searchParams: Promise.resolve({ cursor: "next" }),
      })
    ).alternates?.canonical,
  ).toBe("https://devfeed.tech/tags/c%2B%2B?cursor=next");
});

it("uses separate canonicals for later directory pages and normalizes zero offsets", async () => {
  expect(
    await topicsMetadata({
      searchParams: Promise.resolve({ offset: "00060", utm_source: "email" }),
    }),
  ).toMatchObject({ alternates: { canonical: "https://devfeed.tech/topics?offset=60" } });
  expect(await sourcesMetadata({ searchParams: Promise.resolve({ offset: "0" }) })).toMatchObject({
    alternates: { canonical: "https://devfeed.tech/sources" },
  });
});

it("redirects legacy tag queries to canonical tag routes without losing the cursor", async () => {
  await expect(
    Home({ searchParams: Promise.resolve({ tag: "c++", cursor: "next" }) }),
  ).rejects.toThrow("REDIRECT:/tags/c%2B%2B?cursor=next");
  await expect(
    ContentFeed({
      params: Promise.resolve({ contentType: "tutorials" }),
      searchParams: Promise.resolve({ tag: "c++" }),
    }),
  ).rejects.toThrow("REDIRECT:/tags/c%2B%2B/tutorials");
});
it.each([
  ["articles", "article"],
  ["news", "news"],
  ["tutorials", "tutorial"],
  ["releases", "release"],
  ["comparisons", "comparison"],
  ["opinions", "opinion"],
])("serves /%s with the %s filter and preserves search and pagination", async (path, type) => {
  const props = {
    params: Promise.resolve({ contentType: path }),
    searchParams: Promise.resolve({ q: "python", cursor: "next" }),
  };
  await (path === "articles" ? Articles(props) : ContentFeed(props));
  expect(FeedView).toHaveBeenCalledWith(
    expect.objectContaining({
      filters: expect.objectContaining({ content_type: type, q: "python", cursor: "next" }),
    }),
  );
  await expect(
    Home({ searchParams: Promise.resolve({ content_type: type, cursor: "next" }) }),
  ).rejects.toThrow(`REDIRECT:/${path}?cursor=next`);
});
it("redirects query-style topic and source filters to typed paths", async () => {
  await expect(
    TopicPage({
      params: Promise.resolve({ slug: "python" }),
      searchParams: Promise.resolve({ content_type: "tutorial", q: "guide" }),
    }),
  ).rejects.toThrow("REDIRECT:/topics/python/tutorials?q=guide");
  await expect(
    SourcePage({
      params: Promise.resolve({ id: source.slug }),
      searchParams: Promise.resolve({ content_type: "news", cursor: "next" }),
    }),
  ).rejects.toThrow(`REDIRECT:/sources/${source.slug}/news?cursor=next`);
});
it("uses the content type in the path and removes conflicting query values", async () => {
  await expect(
    ContentFeed({
      params: Promise.resolve({ contentType: "news" }),
      searchParams: Promise.resolve({ content_type: "article", q: "python" }),
    }),
  ).rejects.toThrow("REDIRECT:/news?q=python");
  await expect(
    ContentFeed({
      params: Promise.resolve({ contentType: "news" }),
      searchParams: Promise.resolve({ topic: "python" }),
    }),
  ).rejects.toThrow("REDIRECT:/topics/python/news");
});
it("rejects unknown content paths and provides indexable metadata for type feeds", async () => {
  const searchParams = Promise.resolve({});
  await expect(
    ContentFeed({ params: Promise.resolve({ contentType: "unknown" }), searchParams }),
  ).rejects.toThrow("NOT_FOUND");
  await expect(
    TopicPage({
      params: Promise.resolve({ slug: "python", contentType: "unknown" }),
      searchParams,
    }),
  ).rejects.toThrow("NOT_FOUND");
  await expect(
    SourcePage({
      params: Promise.resolve({ id: source.id, contentType: "unknown" }),
      searchParams,
    }),
  ).rejects.toThrow("NOT_FOUND");
  const metadata = await contentMetadata({
    params: Promise.resolve({ contentType: "tutorials" }),
    searchParams,
  });
  expect(metadata.title).toBe("Tutorials");
  expect(metadata.robots).toBeUndefined();
});
it("redirects old topic links to a stable path without losing filters or cursor", async () => {
  await expect(
    Home({
      searchParams: Promise.resolve({
        topic: "typescript",
        q: "types",
        cursor: "next",
      }),
    }),
  ).rejects.toThrow("REDIRECT:/topics/typescript?q=types&cursor=next");
});
it("redirects old source links to the source page", async () => {
  await expect(
    Home({
      searchParams: Promise.resolve({ source_id: source.id, language: "en" }),
    }),
  ).rejects.toThrow(`REDIRECT:/sources/${source.id}?language=en`);
});
it("uses the topic in the route even when a conflicting query is supplied", async () => {
  await TopicPage({
    params: Promise.resolve({ slug: "typescript", contentType: "tutorials" }),
    searchParams: Promise.resolve({ topic: "rust" }),
  });
  expect(api.getTopic).toHaveBeenCalledWith("typescript");
  expect(FeedView).toHaveBeenCalledWith(
    expect.objectContaining({
      title: topic.name,
      filters: expect.objectContaining({
        topic: "typescript",
        content_type: "tutorial",
      }),
    }),
  );
});
it("uses the source in the route and preserves paging", async () => {
  await SourcePage({
    params: Promise.resolve({ id: source.slug }),
    searchParams: Promise.resolve({ source_id: article.id, cursor: "next" }),
  });
  expect(FeedView).toHaveBeenCalledWith(
    expect.objectContaining({
      title: source.name,
      filters: expect.objectContaining({
        source_id: source.id,
        cursor: "next",
      }),
    }),
  );
});
it("gives topics, sources and articles their own metadata", async () => {
  expect(
    await topicMetadata({
      params: Promise.resolve({ slug: topic.slug }),
      searchParams: Promise.resolve({}),
    }),
  ).toMatchObject({ title: topic.name, description: topic.description });
  expect(
    await sourceMetadata({
      params: Promise.resolve({ id: source.slug }),
      searchParams: Promise.resolve({}),
    }),
  ).toMatchObject({ title: source.name });
  expect(await articleMetadata({ params: Promise.resolve({ slug: article.slug }) })).toMatchObject({
    title: article.title,
    description: article.summary,
  });
});
it("keeps refined query variants out of the index", async () => {
  expect(
    await topicMetadata({
      params: Promise.resolve({ slug: topic.slug }),
      searchParams: Promise.resolve({ q: "types" }),
    }),
  ).toMatchObject({ robots: { index: false, follow: true } });
});
it("returns not-found for unknown topics and sources without hiding upstream outages", async () => {
  vi.mocked(api.getTopic).mockRejectedValue(new api.UserApiError(404));
  await expect(
    TopicPage({
      params: Promise.resolve({ slug: "missing" }),
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow("NOT_FOUND");
  await expect(
    SourcePage({
      params: Promise.resolve({ id: "bad/id" }),
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow("NOT_FOUND");
  expect(api.getSource).not.toHaveBeenCalled();
  vi.mocked(api.getSource).mockRejectedValue(new api.UserApiError(404));
  await expect(
    SourcePage({
      params: Promise.resolve({ id: source.slug }),
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow("NOT_FOUND");
  vi.mocked(api.getTopic).mockRejectedValue(new api.UserApiError(503));
  await expect(
    TopicPage({
      params: Promise.resolve({ slug: topic.slug }),
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toMatchObject({ status: 503 });
});

it("redirects sign-in and registration directly to the provider flow", async () => {
  const { default: Login } = await import("@/app/login/page");
  const { default: Register } = await import("@/app/register/page");
  await expect(Login({ searchParams: Promise.resolve({}) })).rejects.toThrow(
    "REDIRECT:/api/v1/user/auth/login",
  );
  expect(() => Register()).toThrow("REDIRECT:/api/v1/user/auth/login?register=true");
});

it("redirects legacy article IDs to the stable slug", async () => {
  await expect(articleMetadata({ params: Promise.resolve({ slug: article.id }) })).rejects.toThrow(
    `REDIRECT:/articles/${article.slug}`,
  );
});
it("rejects invalid or missing article slugs without masking API outages", async () => {
  await expect(articleMetadata({ params: Promise.resolve({ slug: "../admin" }) })).rejects.toThrow(
    "NOT_FOUND",
  );
  expect(api.getArticle).not.toHaveBeenCalled();
  vi.mocked(api.getArticle).mockRejectedValue(new api.UserApiError(404));
  await expect(
    articleMetadata({ params: Promise.resolve({ slug: "missing-42" }) }),
  ).rejects.toThrow("NOT_FOUND");
  vi.mocked(api.getArticle).mockRejectedValue(new api.UserApiError(503));
  await expect(
    articleMetadata({ params: Promise.resolve({ slug: article.slug }) }),
  ).rejects.toMatchObject({ status: 503 });
});

it("redirects legacy source UUIDs while retaining typed routes and repeated query parameters", async () => {
  await expect(
    SourcePage({
      params: Promise.resolve({ id: source.id, contentType: "tutorials" }),
      searchParams: Promise.resolve({
        cursor: "next+/=",
        language: "en",
        utm_source: ["one", "two"],
      }),
    }),
  ).rejects.toThrow(
    `REDIRECT:/sources/${source.slug}/tutorials?cursor=next%2B%2F%3D&language=en&utm_source=one&utm_source=two`,
  );
  expect(FeedView).not.toHaveBeenCalled();
});

it("redirects source aliases during metadata generation before streaming HTML", async () => {
  await expect(
    sourceMetadata({
      params: Promise.resolve({ id: source.id, contentType: "news" }),
      searchParams: Promise.resolve({ language: "en" }),
    }),
  ).rejects.toThrow(`REDIRECT:/sources/${source.slug}/news?language=en`);
});
