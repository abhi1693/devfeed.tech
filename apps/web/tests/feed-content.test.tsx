// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
      ad_formats: ["feed", "sponsored_content"],
    });
  },
);

it("uses contextual formats when the descriptor does not grant sponsored fallback", () => {
  expect(placementContext(readerFeeds.contextual)).toEqual({
    surface: "feed",
    allow_sponsored_fallback: false,
    ad_formats: ["feed", "sponsored_content"],
  });
});

it.each([{ q: "rust" }, { topic: "rust" }, { tag: "rust" }, { content_type: "news" }])(
  "keeps main-feed placements contextual when effective filters constrain reading: %j",
  (query) => {
    expect(placementContext(readerFeeds.latest, query)).toEqual({
      surface: "feed",
      allow_sponsored_fallback: false,
      ad_formats: ["feed", "sponsored_content"],
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
    ad_formats: ["feed"],
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
    ad_formats: ["feed", "sponsored_content"],
  });
});

it("uses visible articles rather than the broader topic when a source filter is active", () => {
  expect(
    placementContext(
      readerFeeds.contextual,
      { topic: "rust", source_id: "22222222-2222-4222-8222-222222222222" },
      { section: "topics", topicId: "11111111-1111-4111-8111-111111111111" },
    ),
  ).toEqual({
    surface: "feed",
    allow_sponsored_fallback: false,
    ad_formats: ["feed", "sponsored_content"],
  });
});

it.each([readerFeeds.home, readerFeeds.latest, readerFeeds.contextual])(
  "uses inline Feed placements for source articles with descriptor %j",
  (descriptor) => {
    expect(
      placementContext(
        descriptor,
        { source_id: "11111111-1111-4111-8111-111111111111" },
        { section: "sources" },
      ),
    ).toEqual({
      surface: "feed",
      allow_sponsored_fallback: false,
      ad_formats: ["feed", "sponsored_content"],
    });
  },
);

it("keeps a source-filtered latest feed contextual to its visible articles", () => {
  expect(
    placementContext(readerFeeds.latest, {
      source_id: "11111111-1111-4111-8111-111111111111",
    }),
  ).toEqual({
    surface: "feed",
    allow_sponsored_fallback: false,
    ad_formats: ["feed", "sponsored_content"],
  });
});

it.each(["feed", "sources", "topics"] as const)(
  "does not request placements for a paginated %s page",
  (section) => {
    expect(
      placementContext(
        readerFeeds.contextual,
        { cursor: "next", source_id: "11111111-1111-4111-8111-111111111111" },
        { section, topicId: "11111111-1111-4111-8111-111111111111" },
      ),
    ).toBeUndefined();
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

it.each(["source", "topic"] as const)(
  "shares the canonical %s beside its follow action",
  (kind) => {
    const sourceId = "11111111-1111-4111-8111-111111111111";
    render(
      <FeedContent
        filters={{
          ...parseFilters({
            source_id: kind === "source" ? sourceId : "",
            topic: kind === "topic" ? "typed & safe" : "",
            q: "private query",
            cursor: "page-2",
            tag: "private-tag",
          }),
          source_slug: "engineering-journal",
        }}
        title="Engineering & TypeScript"
        section={kind === "source" ? "sources" : "topics"}
        topicId={kind === "topic" ? "topic-id" : undefined}
        feedDescriptor={readerFeeds.contextual}
        feed={firstPage}
        options={options}
      />,
    );
    const trigger = screen.getByRole("button", { name: `Share ${kind}: Engineering & TypeScript` });
    expect(trigger.parentElement?.className).toBe("feed-heading-actions");
    expect(trigger.parentElement?.firstElementChild).toBe(trigger);
    expect(trigger.textContent).toBe("Share");
    fireEvent.click(trigger);
    const panel = screen.getByRole("dialog", { name: `Share ${kind}` });
    const canonical = new URL(
      kind === "source" ? "/sources/engineering-journal" : "/topics/typed%20%26%20safe",
      window.location.origin,
    ).href;
    for (const link of within(panel).getAllByRole("link")) {
      expect(new URL(link.getAttribute("href")!).searchParams.get("url")).toBe(canonical);
      expect(link.getAttribute("rel")).toContain("noopener");
    }
  },
);
