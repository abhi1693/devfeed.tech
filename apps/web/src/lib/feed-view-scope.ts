import { contentTypes, type ContentType, type FeedFilters } from "./feed-query";
import type { FeedDescriptor } from "./reader-feed";
import { normalizeFeedView, type FeedViewPreference } from "./reader-layouts";
import { readerColumnFeedKey } from "./reader-preferences";

export type FeedViewScope =
  | "my_feed"
  | "latest"
  | "trending"
  | "read_later"
  | "search"
  | `content:${ContentType}`
  | `topic:${string}`
  | `source:${string}`
  | `tag:${string}`;

export const guestFeedViewsKey = "devfeed:feed-views:guest";
export const maxFeedViewOverrides = 256;
const scopes = new Set(["my_feed", "latest", "trending", "read_later", "search"]);
const views = new Set<unknown>(["cards", "compact", "stacked", "columns"]);
type GuestStorage = Pick<Storage, "getItem" | "setItem">;
type GuestViews = { version: 1; default_view: FeedViewPreference; views: Record<string, unknown> };

/** Persist feed identity, never a cursor, query, slug alias, or foreground preview URL. */
export function parseFeedViewScope(value: unknown): FeedViewScope | null {
  if (typeof value !== "string") return null;
  if (scopes.has(value)) return value as FeedViewScope;
  if (value.startsWith("content:") && contentTypes.includes(value.slice(8) as ContentType))
    return value as FeedViewScope;
  const entity = /^(topic|source|tag):(.+)$/.exec(value);
  if (!entity) return null;
  const key = readerColumnFeedKey({ kind: entity[1] as "topic" | "source" | "tag", id: entity[2] });
  return /^(topic|source|tag):[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/.test(key)
    ? (key as FeedViewScope)
    : null;
}

export function deriveFeedViewScope({
  descriptor,
  filters,
  topicId,
  tagId,
  authenticated = false,
}: {
  descriptor: FeedDescriptor;
  filters: FeedFilters;
  topicId?: string;
  tagId?: string;
  authenticated?: boolean;
}): FeedViewScope {
  if (descriptor.mode === "bookmarks") return "read_later";
  if (descriptor.mode === "trending") return "trending";
  if (descriptor.mode === "search" || filters.q) return "search";
  const entity =
    (topicId && parseFeedViewScope(`topic:${topicId}`)) ||
    (tagId && parseFeedViewScope(`tag:${tagId}`)) ||
    (filters.source_id && parseFeedViewScope(`source:${filters.source_id}`));
  if (entity) return entity;
  if (descriptor.mode === "personal" && authenticated) return "my_feed";
  if (filters.content_type) {
    const content = parseFeedViewScope(`content:${filters.content_type}`);
    if (content) return content;
  }
  return "latest";
}

/** Legacy query links need their public slug resolved before layout controls can save. */
export function pendingFeedScopeSelection({
  filters,
  topicId,
  tagId,
}: {
  filters: FeedFilters;
  topicId?: string;
  tagId?: string;
}): { kind: "topics" | "tags"; slug: string } | undefined {
  if (filters.q) return undefined;
  if (filters.topic && !parseFeedViewScope(`topic:${topicId}`))
    return { kind: "topics", slug: filters.topic };
  if (filters.tag && !parseFeedViewScope(`tag:${tagId}`))
    return { kind: "tags", slug: filters.tag };
  return undefined;
}

export function effectiveFeedView(value: unknown, scope: FeedViewScope): FeedViewPreference {
  const view = normalizeFeedView(value);
  return view === "columns" && scope !== "my_feed" ? "cards" : view;
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function readStore(storage: Pick<Storage, "getItem">, legacyView: unknown): GuestViews | null {
  const raw = storage.getItem(guestFeedViewsKey);
  if (raw === null) return { version: 1, default_view: normalizeFeedView(legacyView), views: {} };
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { version: 1, default_view: normalizeFeedView(legacyView), views: {} };
  }
  if (!record(value)) return { version: 1, default_view: normalizeFeedView(legacyView), views: {} };
  if (value.version !== 1) return null;
  let normalized: Record<string, unknown> = {};
  if (record(value.views)) {
    if (Object.keys(value.views).length > maxFeedViewOverrides) return null;
    // Preserve future entries and include them in the bound, just as account storage does.
    normalized = { ...value.views };
    for (const [key, view] of Object.entries(value.views)) {
      const scope = parseFeedViewScope(key);
      if (scope && key !== scope && views.has(view)) {
        if (!Object.hasOwn(normalized, scope)) normalized[scope] = view;
        delete normalized[key];
      }
    }
  }
  return {
    version: 1,
    default_view: views.has(value.default_view)
      ? (value.default_view as FeedViewPreference)
      : normalizeFeedView(legacyView),
    views: normalized,
  };
}

/** Reading an untouched feed never creates an override or consumes a first-use marker. */
export function readGuestFeedView(
  scope: FeedViewScope,
  legacyView: unknown,
  storage?: Pick<Storage, "getItem">,
): { view: FeedViewPreference; unavailable: boolean } {
  const guestView = (value: unknown) => {
    const view = effectiveFeedView(value, scope);
    return view === "columns" ? "cards" : view;
  };
  try {
    const stored = readStore(storage ?? window.localStorage, legacyView);
    if (!stored) return { view: guestView(legacyView), unavailable: true };
    const override = stored.views[parseFeedViewScope(scope) ?? scope];
    return {
      view: guestView(views.has(override) ? override : stored.default_view),
      unavailable: false,
    };
  } catch {
    return { view: guestView(legacyView), unavailable: false };
  }
}

export function writeGuestFeedView(
  scope: FeedViewScope,
  view: FeedViewPreference,
  legacyView: unknown,
  storage?: GuestStorage,
): boolean {
  const key = parseFeedViewScope(scope);
  if (!key || !views.has(view) || view === "columns") return false;
  try {
    const target = storage ?? window.localStorage;
    const stored = readStore(target, legacyView);
    if (
      !stored ||
      (!Object.hasOwn(stored.views, key) &&
        Object.keys(stored.views).length >= maxFeedViewOverrides)
    )
      return false;
    target.setItem(
      guestFeedViewsKey,
      JSON.stringify({ ...stored, views: { ...stored.views, [key]: view } }),
    );
    return true;
  } catch {
    return false;
  }
}
