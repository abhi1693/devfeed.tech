import { describe, expect, it } from "vitest";
import {
  displayDate,
  contentTypeFromRoute,
  feedHref,
  feedParams,
  latestFeedParams,
  personalFeedHref,
  outboundArticleUrl,
  displayHost,
  parseFilters,
  safeExternalUrl,
} from "@/lib/feed-query";

describe("user filters", () => {
  it.each([
    ["article", "articles"],
    ["news", "news"],
    ["tutorial", "tutorials"],
    ["release", "releases"],
    ["comparison", "comparisons"],
    ["opinion", "opinions"],
  ])("routes %s feeds through /%s while keeping API query parameters", (type, path) => {
    const filters = parseFilters({ content_type: type });
    expect(feedHref(filters)).toBe(`/${path}`);
    expect(contentTypeFromRoute(path)).toBe(type);
    expect(feedParams(filters).get("content_type")).toBe(type);
    expect(feedHref(filters, { content_type: "" })).toBe("/latest");
  });
  it("retains the source when changing content types or clearing the type", () => {
    const filters = parseFilters({
      source_id: "11111111-1111-4111-8111-111111111111",
      content_type: "news",
    });
    expect(feedHref(filters)).toBe(`/sources/${filters.source_id}/news`);
    expect(feedHref(filters, { content_type: "article" })).toBe(
      `/sources/${filters.source_id}/articles`,
    );
    expect(feedHref(filters, { content_type: "" })).toBe(`/sources/${filters.source_id}`);
  });
  it("accepts supported filters and rejects invalid enum, language and source values", () => {
    const filters = parseFilters({
      q: "  type safety ",
      topic: ["typescript", "other"],
      language: "en-US",
      content_type: "popular",
      source_id: "bad",
    });
    expect(filters).toMatchObject({
      q: "type safety",
      topic: "typescript",
      content_type: "",
      source_id: "",
    });
  });
  it("keeps filters but resets the cursor when changing views", () => {
    const filters = parseFilters({
      q: "C++ & memory",
      topic: "cpp",
      cursor: "old",
      tag: "tools",
    });
    const url = new URL(feedHref(filters, { content_type: "tutorial" }), "http://localhost");
    expect(url.searchParams.get("q")).toBe("C++ & memory");
    expect(url.searchParams.get("cursor")).toBeNull();
    expect(url.pathname).toBe("/topics/cpp/tutorials");
    expect(url.searchParams.has("topic")).toBe(false);
    expect(url.searchParams.has("content_type")).toBe(false);
  });
  it("keeps opaque cursors for explicit pagination", () => {
    const filters = parseFilters({
      language: "pt-br",
      source_id: "11111111-1111-4111-8111-111111111111",
    });
    expect(
      new URL(feedHref(filters, { cursor: "a+b/=" }), "http://localhost").searchParams.get(
        "cursor",
      ),
    ).toBe("a+b/=");
    expect(feedParams(filters).has("language")).toBe(false);
  });
  it("bounds untrusted query input", () => {
    expect(parseFilters({ q: "a".repeat(500), cursor: "b".repeat(500) }).q).toHaveLength(200);
    expect(parseFilters({ cursor: "b".repeat(500) }).cursor).toHaveLength(300);
  });
});
it.each([
  "javascript:alert(1)",
  "data:text/html,test",
  "file:///tmp/test",
  "https://user:password@example.com",
  "invalid",
])("does not render unsafe external links: %s", (value) =>
  expect(safeExternalUrl(value)).toBeUndefined(),
);
it("formats publisher links and dates without locale-dependent hydration", () => {
  expect(safeExternalUrl("https://example.com/story")).toBe("https://example.com/story");
  expect(displayDate("invalid")).toBe("");
  expect(displayDate("2026-09-11T00:00:00Z")).toBe("Sep 11, 2026");
});

it("keeps stable source slugs in navigation and UUIDs in API filters", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  const filters = {
    ...parseFilters({ source_id: id, source_slug: "untrusted" }),
    source_slug: "github-engineering",
  };
  expect(parseFilters({ source_slug: "untrusted" })).not.toHaveProperty("source_slug");
  expect(feedHref(filters, { content_type: "tutorial", cursor: "next" })).toBe(
    "/sources/github-engineering/tutorials?cursor=next",
  );
  expect(feedParams(filters).toString()).toBe(`source_id=${id}`);
  expect(feedHref(filters, { source_id: "" })).toBe("/latest");
  expect(feedHref(filters, { source_id: "22222222-2222-4222-8222-222222222222" })).toBe(
    "/sources/22222222-2222-4222-8222-222222222222",
  );
});

it("requests diverse Latest pages without changing source, topic, tag or search ordering", () => {
  expect(latestFeedParams(parseFilters({})).get("diverse")).toBe("true");
  expect(
    latestFeedParams(parseFilters({ content_type: "news", language: "en" })).get("diverse"),
  ).toBe("true");
  for (const filters of [
    { q: "database" },
    { topic: "database" },
    { tag: "database" },
    { source_id: "11111111-1111-4111-8111-111111111111" },
  ])
    expect(latestFeedParams(parseFilters(filters)).has("diverse")).toBe(false);
});

it.each(["newest", "oldest", "most_liked"])(
  "uses explicit %s ordering instead of publisher interleaving",
  (sort) => {
    const params = latestFeedParams(parseFilters({ sort }));
    expect(params.get("sort")).toBe(sort);
    expect(params.has("diverse")).toBe(false);
  },
);

it("keeps personal feed filters while clearing stale pagination", () => {
  expect(personalFeedHref(parseFilters({}))).toBe("/");
  const filters = parseFilters({ q: "C++", cursor: "old", sort: "newest" });
  expect(personalFeedHref(filters, { sort: "oldest" })).toBe("/?q=C%2B%2B&sort=oldest");
  expect(personalFeedHref(filters, { cursor: "next" })).toBe("/?q=C%2B%2B&sort=newest&cursor=next");
  expect(filters.cursor).toBe("old");
});

it("encodes tag routes and gives topic filters priority", () => {
  expect(feedHref(parseFilters({ tag: "C++ & Rust" }))).toBe("/tags/C%2B%2B%20%26%20Rust");
  expect(feedHref(parseFilters({ topic: "C++", tag: "rust" }))).toBe("/topics/C%2B%2B?tag=rust");
  expect(parseFilters({ topic: "x".repeat(101), tag: "y".repeat(101) })).toMatchObject({
    topic: "x".repeat(100),
    tag: "y".repeat(100),
  });
  expect(parseFilters({ q: [], sort: "unknown" })).toMatchObject({ q: "", sort: "" });
  expect(contentTypeFromRoute("unknown")).toBeUndefined();
});

it("removes the personal ranking sort from Latest without enabling diverse ordering", () => {
  const params = latestFeedParams(parseFilters({ sort: "recommended" }));
  expect(params.has("sort")).toBe(false);
  expect(params.has("diverse")).toBe(false);
});

it("adds publisher attribution while preserving the article URL", () => {
  expect(outboundArticleUrl("https://www.example.com/a?q=rust&utm_source=old#section")).toBe(
    "https://www.example.com/a?q=rust&utm_source=devfeed#section",
  );
  expect(outboundArticleUrl("javascript:alert(1)")).toBeUndefined();
  expect(safeExternalUrl(null)).toBeUndefined();
  expect(safeExternalUrl(undefined)).toBeUndefined();
  expect(safeExternalUrl("http://example.com/a")).toBe("http://example.com/a");
  expect(safeExternalUrl("https://:password@example.com/a")).toBeUndefined();
  expect(displayHost("https://www.example.com:443/a")).toBe("example.com");
  expect(displayHost("https://news.example.com/a")).toBe("news.example.com");
  expect(displayHost("invalid")).toBe("Original publisher");
});
