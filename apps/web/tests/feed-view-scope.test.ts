import { expect, it, vi } from "vitest";
import { parseFilters } from "@/lib/feed-query";
import { readerFeeds } from "@/lib/reader-feed";
import {
  deriveFeedViewScope,
  effectiveFeedView,
  guestFeedViewsKey,
  maxFeedViewOverrides,
  parseFeedViewScope,
  pendingFeedScopeSelection,
  readGuestFeedView,
  writeGuestFeedView,
} from "@/lib/feed-view-scope";

const id = "abcdef01-1234-4567-89ab-abcdef012345";
function storage(initial?: object) {
  const values = new Map(initial ? [[guestFeedViewsKey, JSON.stringify(initial)]] : []);
  return {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => void values.set(key, value)),
    saved: () => JSON.parse(values.get(guestFeedViewsKey) ?? "null"),
  };
}

it.each(["my_feed", "latest", "trending", "read_later", "search", "content:news"])(
  "accepts semantic feed scope %s",
  (scope) => expect(parseFeedViewScope(scope)).toBe(scope),
);
it.each([id.toUpperCase(), id.replaceAll("-", ""), `{${id}}`, `urn:uuid:${id}`])(
  "canonicalizes entity UUID %s",
  (value) => expect(parseFeedViewScope(`topic:${value}`)).toBe(`topic:${id}`),
);
it.each([null, "", "/latest", "source:publisher-slug", "topic:123", "content:unknown", "Latest"])(
  "rejects unstable or unknown scope %j",
  (value) => expect(parseFeedViewScope(value)).toBeNull(),
);

it("derives stable feed identity from declared route metadata without pagination or sort", () => {
  const filters = parseFilters({ cursor: "page-2", sort: "latest" });
  expect(deriveFeedViewScope({ descriptor: readerFeeds.home, filters })).toBe("latest");
  expect(deriveFeedViewScope({ descriptor: readerFeeds.home, filters, authenticated: true })).toBe(
    "my_feed",
  );
  expect(deriveFeedViewScope({ descriptor: readerFeeds.latest, filters })).toBe("latest");
  expect(deriveFeedViewScope({ descriptor: readerFeeds.bookmarks, filters })).toBe("read_later");
  expect(deriveFeedViewScope({ descriptor: readerFeeds.trending, filters })).toBe("trending");
});

it("groups entity content tabs together while keeping each entity and search separate", () => {
  const filters = parseFilters({ content_type: "news", source_id: id, topic: "alias" });
  expect(deriveFeedViewScope({ descriptor: readerFeeds.contextual, filters, topicId: id })).toBe(
    `topic:${id}`,
  );
  expect(deriveFeedViewScope({ descriptor: readerFeeds.contextual, filters, tagId: id })).toBe(
    `tag:${id}`,
  );
  expect(deriveFeedViewScope({ descriptor: readerFeeds.contextual, filters })).toBe(`source:${id}`);
  expect(
    deriveFeedViewScope({
      descriptor: readerFeeds.home,
      filters: parseFilters({ content_type: "news" }),
      authenticated: true,
    }),
  ).toBe("my_feed");
  expect(
    deriveFeedViewScope({
      descriptor: readerFeeds.latest,
      filters: parseFilters({ content_type: "news" }),
    }),
  ).toBe("content:news");
  expect(
    deriveFeedViewScope({
      descriptor: readerFeeds.home,
      filters: { ...filters, q: "react" },
      topicId: id,
    }),
  ).toBe("search");
  expect(deriveFeedViewScope({ descriptor: readerFeeds.search, filters: parseFilters({}) })).toBe(
    "search",
  );
});

it("allows account Columns only on My feed and normalizes unknown layouts", () => {
  expect(effectiveFeedView("columns", "my_feed")).toBe("columns");
  expect(effectiveFeedView("columns", "latest")).toBe("cards");
  expect(effectiveFeedView("future", "my_feed")).toBe("cards");
});

it("requires unresolved legacy topic and tag links to resolve before choosing a saved scope", () => {
  expect(pendingFeedScopeSelection({ filters: parseFilters({ topic: "python" }) })).toEqual({
    kind: "topics",
    slug: "python",
  });
  expect(pendingFeedScopeSelection({ filters: parseFilters({ tag: "typescript" }) })).toEqual({
    kind: "tags",
    slug: "typescript",
  });
  expect(
    pendingFeedScopeSelection({ filters: parseFilters({ topic: "python" }), topicId: id }),
  ).toBeUndefined();
  expect(
    pendingFeedScopeSelection({ filters: parseFilters({ tag: "typescript" }), tagId: id }),
  ).toBeUndefined();
  expect(
    pendingFeedScopeSelection({ filters: parseFilters({ topic: "python", q: "framework" }) }),
  ).toBeUndefined();
});

it("reads legacy layouts without creating storage or permitting guest Columns", () => {
  const target = storage();
  expect(readGuestFeedView("latest", "stacked", target)).toEqual({
    view: "stacked",
    unavailable: false,
  });
  expect(readGuestFeedView("my_feed", "columns", target).view).toBe("cards");
  expect(target.setItem).not.toHaveBeenCalled();
});

it("freezes the guest legacy baseline on the first save and isolates feed overrides", () => {
  const target = storage();
  expect(writeGuestFeedView("latest", "compact", "stacked", target)).toBe(true);
  expect(writeGuestFeedView(`topic:${id}`, "cards", "compact", target)).toBe(true);
  expect(readGuestFeedView("latest", "cards", target).view).toBe("compact");
  expect(readGuestFeedView(`topic:${id}`, "cards", target).view).toBe("cards");
  expect(readGuestFeedView("trending", "cards", target).view).toBe("stacked");
  expect(target.saved()).toEqual({
    version: 1,
    default_view: "stacked",
    views: { latest: "compact", [`topic:${id}`]: "cards" },
  });
  expect(target.setItem.mock.calls.every(([key]) => key === guestFeedViewsKey)).toBe(true);
  expect(writeGuestFeedView("my_feed", "columns", "cards", target)).toBe(false);
});

it("preserves unknown entries and bounds new overrides while permitting updates at capacity", () => {
  const views = Object.fromEntries(
    Array.from({ length: maxFeedViewOverrides - 1 }, (_, i) => [`future:${i}`, "future-layout"]),
  );
  const target = storage({
    version: 1,
    default_view: "cards",
    views: { ...views, latest: "stacked" },
  });
  expect(writeGuestFeedView("trending", "compact", "cards", target)).toBe(false);
  expect(writeGuestFeedView("latest", "compact", "cards", target)).toBe(true);
  expect(Object.keys(target.saved().views)).toHaveLength(maxFeedViewOverrides);
  expect(target.saved().views["future:0"]).toBe("future-layout");
});

it("uses the frozen baseline for an unknown saved override while preserving the stored value", () => {
  const target = storage({
    version: 1,
    default_view: "stacked",
    views: { latest: "future-layout" },
  });
  expect(readGuestFeedView("latest", "compact", target)).toEqual({
    view: "stacked",
    unavailable: false,
  });
  expect(target.saved().views.latest).toBe("future-layout");
  expect(target.setItem).not.toHaveBeenCalled();
});

it("keeps unsupported storage versions intact and handles denied storage", () => {
  const target = storage({ version: 2, views: { latest: "stacked" } });
  expect(readGuestFeedView("latest", "compact", target)).toEqual({
    view: "compact",
    unavailable: true,
  });
  expect(writeGuestFeedView("latest", "cards", "compact", target)).toBe(false);
  expect(target.setItem).not.toHaveBeenCalled();
  const denied = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: vi.fn(),
  };
  expect(readGuestFeedView("latest", "stacked", denied)).toEqual({
    view: "stacked",
    unavailable: false,
  });
  expect(writeGuestFeedView("latest", "compact", "stacked", denied)).toBe(false);
});
