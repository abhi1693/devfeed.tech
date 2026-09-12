import { describe, expect, it } from "vitest";
import {
  displayDate,
  contentTypeFromRoute,
  feedHref,
  feedParams,
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
    expect(feedHref(filters, { content_type: "" })).toBe("/");
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
      language: "",
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
    expect(feedParams(filters).get("language")).toBe("pt-br");
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
