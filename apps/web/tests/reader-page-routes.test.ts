import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ContentPage, { generateMetadata as contentMetadata } from "@/app/[contentType]/page";
import TopicPage, { generateMetadata as topicMetadata } from "@/app/topics/[slug]/page";
import TypedTopicPage, {
  generateMetadata as typedTopicMetadata,
} from "@/app/topics/[slug]/[contentType]/page";
import SourcePage, { generateMetadata as sourceMetadata } from "@/app/sources/[id]/page";
import TypedSourcePage, {
  generateMetadata as typedSourceMetadata,
} from "@/app/sources/[id]/[contentType]/page";
import TagPage, { generateMetadata as tagMetadata } from "@/app/tags/[slug]/page";
import TypedTagPage, {
  generateMetadata as typedTagMetadata,
} from "@/app/tags/[slug]/[contentType]/page";
import { FeedView } from "@/components/feed-view";
import * as api from "@/lib/api";
import { article, source, topic } from "./fixtures";

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NOT_FOUND");
  },
  permanentRedirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));
vi.mock("@/components/feed-view", () => ({ FeedView: vi.fn().mockResolvedValue(null) }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getTopic: vi.fn(),
  getSource: vi.fn(),
  getTag: vi.fn(),
  getFeed: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTopic).mockResolvedValue(topic);
  vi.mocked(api.getSource).mockResolvedValue(source);
  vi.mocked(api.getTag).mockResolvedValue({ id: "tag", slug: "c++", name: "C++" });
  vi.mocked(api.getFeed).mockResolvedValue({ items: [article], next_cursor: null });
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
});
afterEach(() => vi.unstubAllEnvs());

const collections = [
  {
    kind: "topics",
    page: TopicPage,
    metadata: topicMetadata,
    typedPage: TypedTopicPage,
    typedMetadata: typedTopicMetadata,
    params: { slug: topic.slug, id: topic.slug },
    invalid: [
      "",
      "../private",
      "a/b",
      "a\\b",
      "a\u0000b",
      "x".repeat(101),
      "c++",
      "c#",
      "two words",
      "日本語",
      "%2F",
    ],
  },
  {
    kind: "sources",
    page: SourcePage,
    metadata: sourceMetadata,
    typedPage: TypedSourcePage,
    typedMetadata: typedSourceMetadata,
    params: { slug: source.slug, id: source.slug },
    invalid: ["", "../private", "a/b", "a\\b", "a\u0000b", "x".repeat(201)],
  },
  {
    kind: "tags",
    page: TagPage,
    metadata: tagMetadata,
    typedPage: TypedTagPage,
    typedMetadata: typedTagMetadata,
    params: { slug: "c++", id: "c++" },
    invalid: ["", "../private", "a/b", "a\\b", "a\u0000b", "x".repeat(101)],
  },
] as const;

function expectNoDataRequests() {
  expect(api.getTopic).not.toHaveBeenCalled();
  expect(api.getSource).not.toHaveBeenCalled();
  expect(api.getTag).not.toHaveBeenCalled();
  expect(api.getFeed).not.toHaveBeenCalled();
  expect(FeedView).not.toHaveBeenCalled();
}

describe.each(collections)("$kind page route validation", (collection) => {
  it.each(["unknown", "article", "NEWS", "", "tutorials/extra", "tutorials%2Fnews"])(
    "rejects suffix %j before data requests in both metadata and rendering",
    async (contentType) => {
      const props = {
        params: Promise.resolve({ ...collection.params, contentType }),
        searchParams: Promise.resolve({ content_type: "news" }),
      };
      await expect(collection.typedMetadata(props)).rejects.toThrow("NOT_FOUND");
      await expect(collection.typedPage(props)).rejects.toThrow("NOT_FOUND");
      expectNoDataRequests();
    },
  );

  it.each(collection.invalid)("rejects invalid identifier %j before data requests", async (id) => {
    const params = { id, slug: id };
    const props = { params: Promise.resolve(params), searchParams: Promise.resolve({}) };
    await expect(collection.metadata(props)).rejects.toThrow("NOT_FOUND");
    await expect(collection.page(props)).rejects.toThrow("NOT_FOUND");
    expectNoDataRequests();
  });

  it("keeps unfiltered and typed collection views distinct", async () => {
    await collection.page({
      params: Promise.resolve(collection.params),
      searchParams: Promise.resolve({ q: "engineering", cursor: "page-2" }),
    });
    expect(FeedView).toHaveBeenLastCalledWith(
      expect.objectContaining({
        filters: expect.objectContaining({
          content_type: "",
          q: "engineering",
          cursor: "page-2",
        }),
      }),
    );
    await collection.typedPage({
      params: Promise.resolve({ ...collection.params, contentType: "tutorials" }),
      searchParams: Promise.resolve({ q: "engineering", cursor: "page-2" }),
    });
    expect(FeedView).toHaveBeenLastCalledWith(
      expect.objectContaining({
        filters: expect.objectContaining({
          content_type: "tutorial",
          q: "engineering",
          cursor: "page-2",
        }),
      }),
    );
  });
});

it.each(["c++", "c#", ".net"])("preserves encoded technology tag %s", async (slug) => {
  vi.mocked(api.getTag).mockResolvedValue({ id: "tag", slug, name: slug });
  const props = {
    params: Promise.resolve({ slug, contentType: "news" }),
    searchParams: Promise.resolve({ cursor: "next" }),
  };
  expect(await tagMetadata(props)).toMatchObject({
    alternates: {
      canonical: `https://devfeed.tech/tags/${encodeURIComponent(slug)}/news?cursor=next`,
    },
  });
  await TagPage(props);
  expect(api.getTag).toHaveBeenCalledWith(slug);
  expect(FeedView).toHaveBeenCalledWith(
    expect.objectContaining({
      filters: expect.objectContaining({ tag: slug, content_type: "news" }),
    }),
  );
});

it("retains source alias query values after shared validation", async () => {
  const props = {
    params: Promise.resolve({ id: source.id, contentType: "tutorials" }),
    searchParams: Promise.resolve({ cursor: "next+/=", utm_source: ["one", "two"] }),
  };
  const destination = `/sources/${source.slug}/tutorials?cursor=next%2B%2F%3D&utm_source=one&utm_source=two`;
  await expect(sourceMetadata(props)).rejects.toThrow(`REDIRECT:${destination}`);
  await expect(SourcePage(props)).rejects.toThrow(`REDIRECT:${destination}`);
  expect(FeedView).not.toHaveBeenCalled();
});

it.each(["", "unknown", "article", "NEWS", "n%65ws", "topics/python/news", "news/extra", "latest"])(
  "rejects %j as a top-level content type in metadata and rendering",
  async (contentType) => {
    const props = {
      params: Promise.resolve({ contentType }),
      searchParams: Promise.resolve({}),
    };
    await expect(contentMetadata(props)).rejects.toThrow("NOT_FOUND");
    await expect(ContentPage(props)).rejects.toThrow("NOT_FOUND");
    expectNoDataRequests();
  },
);
