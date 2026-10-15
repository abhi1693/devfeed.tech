import { contentTypeFromRoute, type ContentType } from "./feed-query";
import { readerFeeds, type FeedDescriptor } from "./reader-feed";

export type CatalogKind = "topics" | "sources";
type CollectionKind = CatalogKind | "tags";
export type SettingsPage =
  "profile" | "appearance" | "feed" | "notifications" | "topics" | "sources";

export type ReaderRoute =
  | { type: "local"; page: "catalog"; catalog: CatalogKind }
  | { type: "local"; page: "settings"; settings: SettingsPage }
  | {
      type: "local";
      page: "source-suggestion" | "mcp" | "leaderboard" | "dev-card" | "mcp-authorize";
    }
  | { type: "article"; slug: string }
  | { type: "profile"; username: string }
  | {
      type: "reader";
      feed: FeedDescriptor;
      detail?: { kind: CatalogKind; slug: string; contentType?: ContentType };
      contentType?: ContentType;
    };

const routes = new Map<string, ReaderRoute>([
  ["/", { type: "reader", feed: readerFeeds.home }],
  ["/latest", { type: "reader", feed: readerFeeds.latest }],
  ["/search", { type: "reader", feed: readerFeeds.search }],
  ["/read-later", { type: "reader", feed: readerFeeds.bookmarks }],
  ["/trending", { type: "reader", feed: readerFeeds.trending }],
  ["/topics", { type: "local", page: "catalog", catalog: "topics" }],
  ["/sources", { type: "local", page: "catalog", catalog: "sources" }],
  ["/sources/suggest", { type: "local", page: "source-suggestion" }],
  ["/mcp", { type: "local", page: "mcp" }],
  ["/mcp/authorize", { type: "local", page: "mcp-authorize" }],
  ["/leaderboard", { type: "local", page: "leaderboard" }],
  ["/dev-card", { type: "local", page: "dev-card" }],
  ["/settings", { type: "local", page: "settings", settings: "profile" }],
  ["/settings/profile", { type: "local", page: "settings", settings: "profile" }],
  ["/settings/appearance", { type: "local", page: "settings", settings: "appearance" }],
  ["/settings/feed", { type: "local", page: "settings", settings: "feed" }],
  ["/settings/notifications", { type: "local", page: "settings", settings: "notifications" }],
  ["/settings/topics", { type: "local", page: "settings", settings: "topics" }],
  ["/settings/sources", { type: "local", page: "settings", settings: "sources" }],
]);

const catalogSlug = /^[a-z0-9][a-z0-9-]{0,199}$/i;
const username = /^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$/i;
const collectionSeparator = /[\u0000-\u001f\u007f/\\]/;

/** Accept decoded collection parameters, preserving technology tags such as c++ and c#. */
export function collectionRoute<Kind extends CollectionKind>(
  kind: Kind,
  slug: string,
  suffix?: string,
): { kind: Kind; slug: string; contentType?: ContentType } | null {
  const validSlug =
    kind === "tags"
      ? slug.length > 0 &&
        slug.length <= 100 &&
        slug.isWellFormed() &&
        !collectionSeparator.test(slug)
      : catalogSlug.test(slug) && (kind !== "topics" || slug.length <= 100);
  if (!validSlug) return null;
  if (suffix === undefined) return { kind, slug };
  const contentType = contentTypeFromRoute(suffix);
  return contentType ? { kind, slug, contentType } : null;
}

/** Resolve only routes owned by the embedded reader; unsupported pages stay on the website. */
export function resolveReaderRoute(pathname: string): ReaderRoute | null {
  if (!pathname.startsWith("/") || pathname.includes("?") || pathname.includes("#")) return null;
  const path = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  const known = routes.get(path);
  if (known) return known;
  let segments: string[];
  try {
    segments = path.slice(1).split("/").map(decodeURIComponent);
  } catch {
    return null;
  }
  const [, slug, suffix] = segments;
  const resource = path.slice(1).split("/", 1)[0];
  if (
    (resource === "topics" || resource === "sources") &&
    segments.length >= 2 &&
    segments.length <= 3
  ) {
    const detail = collectionRoute(resource, slug, suffix);
    return detail ? { type: "reader", feed: readerFeeds.contextual, detail } : null;
  }
  if (resource === "articles" && segments.length === 2 && catalogSlug.test(slug))
    return { type: "article", slug };
  if (resource === "users" && segments.length === 2 && username.test(slug))
    return { type: "profile", username: slug };
  const contentType = segments.length === 1 ? contentTypeFromRoute(segments[0]) : undefined;
  return contentType ? { type: "reader", feed: readerFeeds.contextual, contentType } : null;
}
