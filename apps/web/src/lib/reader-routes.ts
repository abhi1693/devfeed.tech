import {
  contentTypeFromRoute,
  contentTypeRoutes,
  contentTypes,
  type ContentType,
} from "./feed-query";
import { readerFeeds, type FeedDescriptor } from "./reader-feed";

const catalogKinds = ["topics", "sources"] as const;
export type CatalogKind = (typeof catalogKinds)[number];
type CollectionKind = CatalogKind | "tags";
const settingsPages = [
  "profile",
  "appearance",
  "feed",
  "notifications",
  "topics",
  "sources",
] as const;
export type SettingsPage = (typeof settingsPages)[number];

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

export type ReaderRouteLabels = { telemetry: string; screen: string };
const label = (telemetry: string, screen = telemetry): ReaderRouteLabels => ({ telemetry, screen });
const articleLabels = label("/articles/:id", "/articles");
const profileLabels = label("/users/:username", "/users/profile");
const feedLabels = {
  personal: label("/"),
  latest: label("/latest"),
  search: label("/search"),
  bookmarks: label("/read-later"),
  trending: label("/trending"),
} satisfies Record<FeedDescriptor["mode"], ReaderRouteLabels>;
const localLabels = {
  "source-suggestion": label("/sources/suggest"),
  mcp: label("/mcp"),
  leaderboard: label("/leaderboard"),
  "dev-card": label("/dev-card"),
  "mcp-authorize": label("/mcp/authorize"),
} satisfies Record<
  Exclude<Extract<ReaderRoute, { type: "local" }>["page"], "settings" | "catalog">,
  ReaderRouteLabels
>;
const contentLabels = Object.fromEntries(
  contentTypes.map((type) => [type, label(`/${contentTypeRoutes[type]}`)]),
) as Record<ContentType, ReaderRouteLabels>;
const settingsLabels = Object.fromEntries(
  settingsPages.map((section) => [section, label(`/settings/${section}`)]),
) as Record<SettingsPage, ReaderRouteLabels>;
const catalogLabels = Object.fromEntries(
  catalogKinds.map((kind) => [kind, label(`/${kind}`)]),
) as Record<CatalogKind, ReaderRouteLabels>;
const collectionLabels = Object.fromEntries(
  catalogKinds.map((kind) => [kind, label(`/${kind}/:id`, `/${kind}/detail`)]),
) as Record<CatalogKind, ReaderRouteLabels>;

/** Project only approved semantic fields; identifiers never enter either label. */
export function readerRouteLabels(route: ReaderRoute | null): ReaderRouteLabels | null {
  if (!route) return null;
  switch (route.type) {
    case "article":
      return articleLabels;
    case "profile":
      return profileLabels;
    case "local":
      if (route.page === "catalog") return catalogLabels[route.catalog];
      if (route.page === "settings") return settingsLabels[route.settings];
      return localLabels[route.page];
    case "reader": {
      if (route.detail) {
        const labels = collectionLabels[route.detail.kind];
        return route.detail.contentType
          ? { ...labels, telemetry: `${labels.telemetry}/:type` }
          : labels;
      }
      return route.contentType ? contentLabels[route.contentType] : feedLabels[route.feed.mode];
    }
  }
}

/** The relay accepts this finite vocabulary, never a client-supplied raw path. */
export const readerScreenNames = [
  ...new Set(
    [
      articleLabels,
      profileLabels,
      ...Object.values(feedLabels),
      ...Object.values(localLabels),
      ...Object.values(contentLabels),
      ...Object.values(settingsLabels),
      ...Object.values(catalogLabels),
      ...Object.values(collectionLabels),
    ].map(({ screen }) => screen),
  ),
];

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

type ReaderTelemetryDefinition = {
  route: string;
  label: string;
  parameters?: Record<string, readonly string[]>;
};
const contentTypeParameters = { contentType: Object.values(contentTypeRoutes) };

/** Build-time metadata for the shared receiver catalog, grounded in registered Next routes. */
export const readerTelemetryDefinitions: readonly ReaderTelemetryDefinition[] = [
  ...Array.from(routes, ([route, descriptor]) => ({
    route,
    label: readerRouteLabels(descriptor)!.telemetry,
  })),
  { route: "/articles/[slug]", label: articleLabels.telemetry },
  { route: "/users/[username]", label: profileLabels.telemetry },
  ...catalogKinds.flatMap((kind) => {
    const route = `/${kind}/[${kind === "sources" ? "id" : "slug"}]`;
    return [
      { route, label: collectionLabels[kind].telemetry },
      {
        route: `${route}/[contentType]`,
        label: `${collectionLabels[kind].telemetry}/:type`,
        parameters: contentTypeParameters,
      },
    ];
  }),
  { route: "/tags/[slug]", label: "/tags/:id" },
  {
    route: "/tags/[slug]/[contentType]",
    label: "/tags/:id/:type",
    parameters: contentTypeParameters,
  },
  ...contentTypes.map((type) => ({
    route: "/[contentType]",
    label: contentLabels[type].telemetry,
    parameters: { contentType: [contentTypeRoutes[type]] },
  })),
];

const catalogSlug = /^[a-z0-9][a-z0-9-]{0,199}$/i;
const username = /^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$/i;
const collectionSeparator = /[\u0000-\u001f\u007f/\\]/;

export function isArticleSlug(slug: string): boolean {
  return catalogSlug.test(slug);
}

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
  if (resource === "articles" && segments.length === 2 && isArticleSlug(slug))
    return { type: "article", slug };
  if (resource === "users" && segments.length === 2 && username.test(slug))
    return { type: "profile", username: slug };
  const contentType = segments.length === 1 ? contentTypeFromRoute(segments[0]) : undefined;
  return contentType ? { type: "reader", feed: readerFeeds.contextual, contentType } : null;
}
