import { parseFilters, type ContentType, type FeedFilters, type SearchParams } from "./feed-query";
import type { PlacementQuery } from "./partner-placements";

export type FeedMode = "latest" | "personal" | "bookmarks" | "search" | "trending";
export type FeedDescriptor = {
  readonly mode: FeedMode;
  readonly guestMode?: "latest";
  readonly capabilities: { readonly placements: boolean; readonly sponsoredFallback: boolean };
};

const mainCapabilities = { placements: true, sponsoredFallback: true } as const;
const contextualCapabilities = { placements: true, sponsoredFallback: false } as const;
const noPlacements = { placements: false, sponsoredFallback: false } as const;

export const readerFeeds = {
  home: { mode: "personal", guestMode: "latest", capabilities: mainCapabilities },
  latest: { mode: "latest", capabilities: mainCapabilities },
  contextual: { mode: "latest", capabilities: contextualCapabilities },
  search: { mode: "search", capabilities: contextualCapabilities },
  bookmarks: { mode: "bookmarks", capabilities: noPlacements },
  trending: { mode: "trending", capabilities: noPlacements },
} as const satisfies Record<string, FeedDescriptor>;

/** Resolve account-dependent mode and route/query filters without inspecting URL paths. */
export function resolveFeed(
  descriptor: FeedDescriptor,
  query: SearchParams,
  authenticated: boolean,
  contentType?: ContentType,
): { mode: FeedMode; filters: FeedFilters } {
  return {
    mode: authenticated ? descriptor.mode : (descriptor.guestMode ?? descriptor.mode),
    filters: parseFilters({
      ...query,
      ...(contentType !== undefined ? { content_type: contentType } : {}),
    }),
  };
}

/** Shared placement eligibility and formats for public and personalized feed renderers. */
export function feedPlacements(
  descriptor: FeedDescriptor,
  filters: FeedFilters,
  {
    section = "feed",
    topicId,
  }: { section?: "feed" | "topics" | "sources" | "tags"; topicId?: string } = {},
): Omit<PlacementQuery, "article_ids"> | undefined {
  if (!descriptor.capabilities.placements || filters.cursor) return undefined;
  if (section === "topics" && topicId && !filters.q && !filters.source_id)
    return { surface: "topic", topic_id: topicId, ad_formats: ["feed"] };
  const fallback =
    descriptor.capabilities.sponsoredFallback &&
    section === "feed" &&
    !topicId &&
    !filters.q &&
    !filters.topic &&
    !filters.source_id &&
    !filters.content_type &&
    !filters.tag;
  return {
    surface: "feed",
    allow_sponsored_fallback: fallback,
    ad_formats: ["feed", "sponsored_content"],
  };
}
