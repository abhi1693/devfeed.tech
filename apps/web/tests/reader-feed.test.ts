import { describe, expect, it } from "vitest";
import { parseFilters } from "@/lib/feed-query";
import { canUseColumnWorkspace, feedPlacements, readerFeeds, resolveFeed } from "@/lib/reader-feed";
import { resolveReaderRoute } from "@/lib/reader-routes";

describe("reader feed descriptors", () => {
  it.each([
    ["/", "latest", "personal", true],
    ["/latest", "latest", "latest", true],
    ["/news", "latest", "latest", false],
    ["/topics/rust", "latest", "latest", false],
    ["/sources/publisher/tutorials", "latest", "latest", false],
    ["/search", "search", "search", false],
    ["/read-later", "bookmarks", "bookmarks", false],
    ["/trending", "trending", "trending", false],
  ] as const)(
    "%s declares its account modes and fallback capability",
    (path, guest, member, fallback) => {
      const route = resolveReaderRoute(path);
      expect(route?.type).toBe("reader");
      if (route?.type !== "reader") throw new Error("Expected reader route");
      expect(resolveFeed(route.feed, {}, false).mode).toBe(guest);
      expect(resolveFeed(route.feed, {}, true).mode).toBe(member);
      expect(route.feed.capabilities.sponsoredFallback).toBe(fallback);
    },
  );

  it("account and guest modes use the same All defaults without an implicit content type", () => {
    for (const descriptor of [readerFeeds.home, readerFeeds.latest])
      for (const authenticated of [false, true]) {
        expect(resolveFeed(descriptor, {}, authenticated).filters.content_type).toBe("");
        expect(
          resolveFeed(descriptor, { content_type: "invalid" }, authenticated).filters.content_type,
        ).toBe("");
      }
  });

  it("keeps route content type authoritative and preserves repeated query semantics", () => {
    const result = resolveFeed(
      readerFeeds.contextual,
      {
        content_type: ["news", "article"],
        topic: ["rust", "javascript"],
        q: ["  compiler ", "ignored"],
        cursor: "page-2",
      },
      false,
      "tutorial",
    );
    expect(result.filters).toMatchObject({
      content_type: "tutorial",
      topic: "rust",
      q: "compiler",
      cursor: "page-2",
    });
  });
});

describe("Columns workspace filter eligibility", () => {
  it.each([
    { q: "compiler" },
    { topic: "rust" },
    { tag: "C++" },
    { source_id: "11111111-1111-4111-8111-111111111111" },
    { content_type: "news" },
  ])("preserves a contextual article selection %j", (query) => {
    expect(canUseColumnWorkspace(parseFilters(query))).toBe(false);
  });

  it.each([
    {},
    { sort: "newest" },
    { sort: "oldest" },
    { sort: "most_liked" },
    { sort: "recommended" },
    { cursor: "page-2", sort: "oldest" },
  ])("allows an unrestricted main feed %j", (query) => {
    expect(canUseColumnWorkspace(parseFilters(query))).toBe(true);
  });

  it("uses effective filters after invalid and blank query values are normalized", () => {
    expect(
      canUseColumnWorkspace(
        parseFilters({
          q: " ",
          topic: " ",
          tag: " ",
          source_id: "invalid",
          content_type: "invalid",
        }),
      ),
    ).toBe(true);
  });
});

describe("shared native feed eligibility", () => {
  it.each(["", "newest", "oldest", "most_liked", "recommended"])(
    "allows main fallback with sort %s on public and personal feeds",
    (sort) => {
      for (const descriptor of [readerFeeds.home, readerFeeds.latest])
        expect(feedPlacements(descriptor, parseFilters({ sort }))).toEqual({
          surface: "feed",
          allow_sponsored_fallback: true,
          ad_formats: ["feed", "sponsored_content"],
        });
    },
  );

  it.each([
    { q: "compiler" },
    { topic: "rust" },
    { tag: "c++" },
    { content_type: "news" },
    { source_id: "11111111-1111-4111-8111-111111111111" },
  ])("keeps filtered feed %j contextual", (query) => {
    expect(feedPlacements(readerFeeds.latest, parseFilters(query))).toEqual({
      surface: "feed",
      allow_sponsored_fallback: false,
      ad_formats: ["feed", "sponsored_content"],
    });
  });

  it("normalizes invalid query values before eligibility", () => {
    const { filters } = resolveFeed(
      readerFeeds.latest,
      {
        content_type: "invalid",
        source_id: "invalid",
        q: " ",
        tag: " ",
      },
      false,
    );
    expect(feedPlacements(readerFeeds.latest, filters)?.allow_sponsored_fallback).toBe(true);
  });

  it("never grants main-feed formats to contextual descriptors", () => {
    for (const descriptor of [readerFeeds.contextual, readerFeeds.search])
      expect(feedPlacements(descriptor, parseFilters({}))).toEqual({
        surface: "feed",
        allow_sponsored_fallback: false,
        ad_formats: ["feed", "sponsored_content"],
      });
  });

  it("preserves topic placements and makes searched topics contextual", () => {
    const context = { section: "topics", topicId: "topic-id" } as const;
    expect(
      feedPlacements(
        readerFeeds.contextual,
        parseFilters({ topic: "rust", content_type: "news" }),
        context,
      ),
    ).toEqual({ surface: "topic", topic_id: "topic-id", ad_formats: ["feed"] });
    expect(feedPlacements(readerFeeds.home, parseFilters({ q: "rust" }), context)).toMatchObject({
      surface: "feed",
      allow_sponsored_fallback: false,
    });
    expect(
      feedPlacements(
        readerFeeds.home,
        parseFilters({ source_id: "11111111-1111-4111-8111-111111111111" }),
        context,
      ),
    ).toEqual({
      surface: "feed",
      allow_sponsored_fallback: false,
      ad_formats: ["feed", "sponsored_content"],
    });
    expect(
      feedPlacements(readerFeeds.home, parseFilters({}), { topicId: "topic-id" }),
    ).toMatchObject({ surface: "feed", allow_sponsored_fallback: false });
  });

  it("uses contextual Feed placements on source article pages", () => {
    for (const descriptor of [readerFeeds.home, readerFeeds.latest, readerFeeds.contextual])
      expect(
        feedPlacements(
          descriptor,
          parseFilters({ source_id: "11111111-1111-4111-8111-111111111111" }),
          { section: "sources" },
        ),
      ).toEqual({
        surface: "feed",
        allow_sponsored_fallback: false,
        ad_formats: ["feed", "sponsored_content"],
      });
  });

  it("suppresses paginated, bookmarks, and trending placements", () => {
    for (const descriptor of [readerFeeds.home, readerFeeds.latest, readerFeeds.contextual]) {
      expect(feedPlacements(descriptor, parseFilters({ cursor: "next" }))).toBeUndefined();
      expect(
        feedPlacements(
          descriptor,
          parseFilters({ source_id: "11111111-1111-4111-8111-111111111111", cursor: "next" }),
          { section: "sources" },
        ),
      ).toBeUndefined();
    }
    for (const descriptor of [readerFeeds.bookmarks, readerFeeds.trending])
      for (const section of ["feed", "topics", "sources"] as const)
        expect(
          feedPlacements(descriptor, parseFilters({}), { section, topicId: "topic-id" }),
        ).toBeUndefined();
  });
});
