import { beforeEach, expect, it, vi } from "vitest";
import Home from "@/app/page";
import ContentFeed, { generateMetadata as contentMetadata } from "@/app/[contentType]/page";
import Articles from "@/app/articles/page";
import TopicPage, {
  generateMetadata as topicMetadata,
} from "@/app/topics/[slug]/page";
import SourcePage, {
  generateMetadata as sourceMetadata,
} from "@/app/sources/[id]/page";
import { generateMetadata as articleMetadata } from "@/app/articles/[id]/page";
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
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTopic).mockResolvedValue(topic);
  vi.mocked(api.getSource).mockResolvedValue(source);
  vi.mocked(api.getArticle).mockResolvedValue(article);
});
it.each([
  ["articles", "article"], ["news", "news"], ["tutorials", "tutorial"],
  ["releases", "release"], ["comparisons", "comparison"], ["opinions", "opinion"],
])("serves /%s with the %s filter and preserves search and pagination", async (path, type) => {
  const props = { params: Promise.resolve({ contentType: path }), searchParams: Promise.resolve({ q: "python", cursor: "next" }) };
  await (path === "articles" ? Articles(props) : ContentFeed(props));
  expect(FeedView).toHaveBeenCalledWith(expect.objectContaining({ filters: expect.objectContaining({ content_type: type, q: "python", cursor: "next" }) }));
  await expect(Home({ searchParams: Promise.resolve({ content_type: type, cursor: "next" }) })).rejects.toThrow(`REDIRECT:/${path}?cursor=next`);
});
it("redirects query-style topic and source filters to typed paths", async () => {
  await expect(TopicPage({ params: Promise.resolve({ slug: "python" }), searchParams: Promise.resolve({ content_type: "tutorial", q: "guide" }) })).rejects.toThrow("REDIRECT:/topics/python/tutorials?q=guide");
  await expect(SourcePage({ params: Promise.resolve({ id: source.id }), searchParams: Promise.resolve({ content_type: "news", cursor: "next" }) })).rejects.toThrow(`REDIRECT:/sources/${source.id}/news?cursor=next`);
});
it("uses the content type in the path and removes conflicting query values", async () => {
  await expect(ContentFeed({ params: Promise.resolve({ contentType: "news" }), searchParams: Promise.resolve({ content_type: "article", q: "python" }) })).rejects.toThrow("REDIRECT:/news?q=python");
  await expect(ContentFeed({ params: Promise.resolve({ contentType: "news" }), searchParams: Promise.resolve({ topic: "python" }) })).rejects.toThrow("REDIRECT:/topics/python/news");
});
it("rejects unknown content paths and provides indexable metadata for type feeds", async () => {
  const searchParams = Promise.resolve({});
  await expect(ContentFeed({ params: Promise.resolve({ contentType: "unknown" }), searchParams })).rejects.toThrow("NOT_FOUND");
  await expect(TopicPage({ params: Promise.resolve({ slug: "python", contentType: "unknown" }), searchParams })).rejects.toThrow("NOT_FOUND");
  await expect(SourcePage({ params: Promise.resolve({ id: source.id, contentType: "unknown" }), searchParams })).rejects.toThrow("NOT_FOUND");
  const metadata = await contentMetadata({ params: Promise.resolve({ contentType: "tutorials" }), searchParams });
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
    params: Promise.resolve({ id: source.id }),
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
      params: Promise.resolve({ id: source.id }),
      searchParams: Promise.resolve({}),
    }),
  ).toMatchObject({ title: source.name });
  expect(
    await articleMetadata({ params: Promise.resolve({ id: article.id }) }),
  ).toMatchObject({ title: article.title, description: article.summary });
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
      params: Promise.resolve({ id: "bad-id" }),
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow("NOT_FOUND");
  expect(api.getSource).not.toHaveBeenCalled();
  vi.mocked(api.getSource).mockRejectedValue(new api.UserApiError(404));
  await expect(
    SourcePage({
      params: Promise.resolve({ id: source.id }),
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
  expect(() => Register()).toThrow(
    "REDIRECT:/api/v1/user/auth/login?register=true",
  );
});
