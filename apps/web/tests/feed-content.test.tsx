// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { FeedContent, type FeedContentProps } from "@/components/feed-content";
import { parseFilters, type FeedFilters, type SearchParams } from "@/lib/feed-query";
import { readerFeeds, type FeedDescriptor } from "@/lib/reader-feed";
import type { PlacementQuery } from "@/lib/partner-placements";
import type { FeedPage, FeedOptions } from "@/lib/types";
import { article } from "./fixtures";
import { readerRequest } from "@/lib/reader-runtime";

const preferences = vi.hoisted(() => ({ view: "cards" }));
const session = vi.hoisted(() => ({ user: null as { user_id: string } | null }));
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));
vi.mock("@/components/scoped-feed-preferences", () => ({
  ScopedFeedPreferences: ({
    scope,
    paused = false,
    children,
  }: {
    scope: string;
    paused?: boolean;
    children: ReactNode;
  }) => (
    <div data-testid="feed-view-scope" data-scope={paused ? undefined : scope} data-paused={paused}>
      {children}
    </div>
  ),
  useFeedViewScope: () => null,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/lib/reader-runtime", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/reader-runtime")>();
  return {
    ...original,
    readerRequest: vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      /^\/api\/v1\/(topics|tags)\//.test(String(input))
        ? Promise.resolve(Response.json({ id: "11111111-1111-4111-8111-111111111111" }))
        : original.readerRequest(input, init),
    ),
  };
});
vi.mock("@/components/columns-workspace", () => ({
  ColumnsWorkspace: () => <section aria-label="Columns workspace" />,
}));

vi.mock("@/components/user-shell", () => ({
  UserShell: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/feed-filters", () => ({ FeedFiltersBar: () => null }));
vi.mock("@/components/source-follow", () => ({
  SourceFollow: ({ returnTo }: { returnTo: string }) => <a href={returnTo}>Follow source</a>,
}));
vi.mock("@/components/topic-follow", () => ({ TopicFollow: () => null }));
vi.mock("@/components/tag-follow", () => ({
  TagFollow: ({ tagId, returnTo }: { tagId: string; returnTo: string }) => (
    <a href={returnTo} data-tag-id={tagId}>
      Follow tag
    </a>
  ),
}));
vi.mock("@/components/infinite-feed", () => ({
  InfiniteFeed: ({
    filters,
    placements,
  }: {
    filters: FeedFilters;
    placements?: PlacementQuery;
  }) => (
    <div
      data-testid="infinite-feed"
      data-filters={JSON.stringify(filters)}
      data-placements={JSON.stringify(placements)}
    />
  ),
}));
afterEach(cleanup);
beforeEach(() => {
  preferences.view = "cards";
  session.user = null;
  vi.mocked(readerRequest).mockClear();
});

const firstPage: PromiseSettledResult<FeedPage> = {
  status: "fulfilled",
  value: { items: [article], next_cursor: null },
};
const options: PromiseSettledResult<FeedOptions> = {
  status: "fulfilled",
  value: { sources: [], content_types: [] },
};

// Server-resolved entity metadata is supplied for these rendering/placement fixtures.
function ResolvedFeedContent(props: FeedContentProps) {
  return (
    <FeedContent
      topicId={props.filters.topic ? "11111111-1111-4111-8111-111111111111" : undefined}
      tagId={props.filters.tag ? "22222222-2222-4222-8222-222222222222" : undefined}
      {...props}
    />
  );
}

it.each([
  [readerFeeds.home, {}, "latest"],
  [readerFeeds.latest, { sort: "most_liked", cursor: "page-2" }, "latest"],
  [readerFeeds.latest, { content_type: "news" }, "content:news"],
  [
    readerFeeds.contextual,
    { topic: "python", content_type: "news" },
    "topic:11111111-1111-4111-8111-111111111111",
  ],
  [readerFeeds.contextual, { tag: "python" }, "tag:22222222-2222-4222-8222-222222222222"],
  [
    readerFeeds.contextual,
    { source_id: "33333333-3333-4333-8333-333333333333" },
    "source:33333333-3333-4333-8333-333333333333",
  ],
  [readerFeeds.home, { q: "python" }, "search"],
] as const)("passes canonical scope %s with filters %j", (descriptor, query, expected) => {
  render(
    <ResolvedFeedContent
      filters={parseFilters(query)}
      feedDescriptor={descriptor}
      feed={firstPage}
      options={options}
    />,
  );
  expect(screen.getByTestId("feed-view-scope").getAttribute("data-scope")).toBe(expected);
});

it.each(["topic", "tag"] as const)(
  "keeps a legacy %s query feed mounted while its scoped controls wait for identity",
  async (kind) => {
    render(
      <FeedContent
        filters={parseFilters({ [kind]: "python alias" })}
        feedDescriptor={readerFeeds.home}
        feed={firstPage}
        options={options}
      />,
    );
    const scope = screen.getByTestId("feed-view-scope");
    const feed = screen.getByTestId("infinite-feed");
    expect(scope.getAttribute("data-paused")).toBe("true");
    expect(scope.hasAttribute("data-scope")).toBe(false);
    await waitFor(() =>
      expect(scope.getAttribute("data-scope")).toBe(`${kind}:11111111-1111-4111-8111-111111111111`),
    );
    expect(screen.getByTestId("feed-view-scope")).toBe(scope);
    expect(screen.getByTestId("infinite-feed")).toBe(feed);
    expect(scope.getAttribute("data-paused")).toBe("false");
    expect(readerRequest).toHaveBeenCalledWith(
      `/api/v1/${kind}s/python%20alias`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  },
);

it.each([undefined, false, true])(
  "keeps Latest articles visible even with a stale Columns permission (%s)",
  (allowColumns) => {
    preferences.view = "columns";
    session.user = { user_id: "reader-a" };
    render(
      <ResolvedFeedContent
        filters={parseFilters({})}
        feedDescriptor={readerFeeds.latest}
        feed={firstPage}
        options={options}
        allowColumns={allowColumns}
      />,
    );
    expect(screen.getByTestId("infinite-feed")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  },
);

it.each([
  { q: "rust" },
  { topic: "rust" },
  { tag: "C++" },
  { source_id: "11111111-1111-4111-8111-111111111111" },
  { content_type: "news" },
])("keeps the guest root feed filtered by %j when Columns is selected", (query) => {
  preferences.view = "columns";
  const filters = parseFilters(query);
  render(
    <ResolvedFeedContent
      filters={filters}
      feedDescriptor={readerFeeds.home}
      feed={firstPage}
      options={options}
      allowColumns
    />,
  );
  expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  expect(JSON.parse(screen.getByTestId("infinite-feed").getAttribute("data-filters")!)).toEqual(
    filters,
  );
});

it.each([{}, { sort: "most_liked" }, { cursor: "next", sort: "oldest" }])(
  "keeps public articles on an unrestricted guest root feed with saved Columns %j",
  (query) => {
    preferences.view = "columns";
    render(
      <ResolvedFeedContent
        filters={parseFilters(query)}
        feedDescriptor={readerFeeds.home}
        feed={firstPage}
        options={options}
        allowColumns
      />,
    );
    expect(screen.getByTestId("infinite-feed")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  },
);

function placementContext(
  descriptor: FeedDescriptor,
  query: SearchParams = {},
  context: Pick<FeedContentProps, "section" | "topicId"> = {},
) {
  render(
    <ResolvedFeedContent
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
    <ResolvedFeedContent
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

it.each(["source", "topic", "tag"] as const)(
  "shares the canonical %s beside its follow action",
  (kind) => {
    const sourceId = "11111111-1111-4111-8111-111111111111";
    render(
      <ResolvedFeedContent
        filters={{
          ...parseFilters({
            source_id: kind === "source" ? sourceId : "",
            topic: kind === "topic" ? "typed & safe" : "",
            q: "private query",
            cursor: "page-2",
            tag: kind === "tag" ? "C++" : "private-tag",
          }),
          source_slug: "engineering-journal",
        }}
        title="Engineering & TypeScript"
        section={kind === "source" ? "sources" : kind === "tag" ? "tags" : "topics"}
        topicId={kind === "topic" ? "topic-id" : undefined}
        tagId={kind === "tag" ? "tag-id" : undefined}
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
      kind === "source"
        ? "/sources/engineering-journal"
        : kind === "tag"
          ? "/tags/C%2B%2B"
          : "/topics/typed%20%26%20safe",
      window.location.origin,
    ).href;
    for (const link of within(panel).getAllByRole("link")) {
      expect(new URL(link.getAttribute("href")!).searchParams.get("url")).toBe(canonical);
      expect(link.getAttribute("rel")).toContain("noopener");
    }
    if (kind === "tag") {
      const follow = screen.getByRole("link", { name: "Follow tag" });
      expect(follow.getAttribute("data-tag-id")).toBe("tag-id");
      expect(follow.getAttribute("href")).toContain("/tags/C%2B%2B");
    }
  },
);

it("uses a subordinate feed heading when the guest introduction supplies the page heading", () => {
  render(
    <FeedContent
      filters={parseFilters({})}
      feedDescriptor={readerFeeds.home}
      feed={firstPage}
      options={options}
      headingLevel={2}
    />,
  );
  expect(screen.getByRole("heading", { name: "Latest feed", level: 2 })).toBeTruthy();
  expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
});
