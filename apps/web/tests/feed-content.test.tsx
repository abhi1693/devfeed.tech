// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { FeedContent, type FeedContentProps } from "@/components/feed-content";
import { parseFilters, type SearchParams } from "@/lib/feed-query";
import { readerFeeds, type FeedDescriptor } from "@/lib/reader-feed";
import type { PlacementQuery } from "@/lib/partner-placements";
import type { FeedPage, FeedOptions } from "@/lib/types";
import { article } from "./fixtures";

vi.mock("@/components/user-shell", () => ({
  UserShell: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/feed-filters", () => ({ FeedFiltersBar: () => null }));
vi.mock("@/components/source-follow", () => ({
  SourceFollow: ({ returnTo }: { returnTo: string }) => <a href={returnTo}>Follow source</a>,
}));
vi.mock("@/components/topic-follow", () => ({ TopicFollow: () => null }));
vi.mock("@/components/infinite-feed", () => ({
  InfiniteFeed: ({ placements }: { placements?: PlacementQuery }) => (
    <div data-testid="infinite-feed" data-placements={JSON.stringify(placements)} />
  ),
}));
afterEach(cleanup);

const firstPage: PromiseSettledResult<FeedPage> = {
  status: "fulfilled",
  value: { items: [article], next_cursor: null },
};
const options: PromiseSettledResult<FeedOptions> = {
  status: "fulfilled",
  value: { sources: [], content_types: [] },
};

function placementContext(
  descriptor: FeedDescriptor,
  query: SearchParams = {},
  context: Pick<FeedContentProps, "section" | "topicId"> = {},
) {
  render(
    <FeedContent
      filters={parseFilters(query)}
      feedDescriptor={descriptor}
      feed={firstPage}
      options={options}
      {...context}
    />,
  );
  const value = screen.getByTestId("infinite-feed").getAttribute("data-placements");
  return value ? (JSON.parse(value) as PlacementQuery) : undefined;
}

it.each([readerFeeds.home, readerFeeds.latest])(
  "allows main feed formats from an explicit %j descriptor",
  (descriptor) => {
    expect(placementContext(descriptor)).toEqual({
      surface: "feed",
      allow_sponsored_fallback: true,
      ad_formats: ["feed", "homepage_sponsorship", "sponsored_content", "engagement"],
    });
  },
);

it("uses contextual formats when the descriptor does not grant sponsored fallback", () => {
  expect(placementContext(readerFeeds.contextual)).toEqual({
    surface: "feed",
    allow_sponsored_fallback: false,
    ad_formats: ["feed", "sponsored_content", "engagement"],
  });
});

it.each([{ q: "rust" }, { topic: "rust" }, { tag: "rust" }, { content_type: "news" }])(
  "keeps main-feed placements contextual when effective filters constrain reading: %j",
  (query) => {
    expect(placementContext(readerFeeds.latest, query)).toEqual({
      surface: "feed",
      allow_sponsored_fallback: false,
      ad_formats: ["feed", "sponsored_content", "engagement"],
    });
  },
);

it("uses the resolved topic as placement context", () => {
  expect(
    placementContext(
      readerFeeds.contextual,
      { topic: "rust", content_type: "tutorial", tag: "backend" },
      { section: "topics", topicId: "11111111-1111-4111-8111-111111111111" },
    ),
  ).toEqual({
    surface: "topic",
    topic_id: "11111111-1111-4111-8111-111111111111",
    ad_formats: ["feed", "engagement"],
  });
});

it("keeps searched topic results contextual to their visible articles", () => {
  expect(
    placementContext(
      readerFeeds.contextual,
      { topic: "rust", q: "async" },
      { section: "topics", topicId: "11111111-1111-4111-8111-111111111111" },
    ),
  ).toEqual({
    surface: "feed",
    allow_sponsored_fallback: false,
    ad_formats: ["feed", "sponsored_content", "engagement"],
  });
});

it.each([
  [{ cursor: "next" }, {}],
  [{ source_id: "11111111-1111-4111-8111-111111111111" }, {}],
  [{}, { section: "sources" }],
] as const)(
  "does not request placements for cursor or source browsing: %j %j",
  (query, context) => {
    expect(placementContext(readerFeeds.latest, query, context)).toBeUndefined();
  },
);

it("honors disabled placement capability even for an unfiltered feed", () => {
  expect(placementContext(readerFeeds.bookmarks)).toBeUndefined();
});

it("preserves the source tab, sort and pagination in the sign-in destination", () => {
  render(
    <FeedContent
      filters={{
        ...parseFilters({
          source_id: "11111111-1111-4111-8111-111111111111",
          content_type: "news",
          sort: "oldest",
          cursor: "next page",
        }),
        source_slug: "publisher",
      }}
      section="sources"
      feedDescriptor={readerFeeds.contextual}
      feed={firstPage}
      options={options}
    />,
  );
  expect(screen.getByRole("link", { name: "Follow source" }).getAttribute("href")).toBe(
    "/sources/publisher/news?sort=oldest&cursor=next+page",
  );
});
