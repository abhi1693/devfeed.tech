export const contentTypes = [
  "article",
  "news",
  "tutorial",
  "release",
  "comparison",
  "opinion",
] as const;
export const contentTypeRoutes = {
  article: "articles",
  news: "news",
  tutorial: "tutorials",
  release: "releases",
  comparison: "comparisons",
  opinion: "opinions",
} as const;
export function contentTypeFromRoute(route: string) {
  return contentTypes.find((type) => contentTypeRoutes[type] === route);
}
export type SearchParams = Record<string, string | string[] | undefined>;
export type FeedFilters = {
  q: string;
  topic: string;
  content_type: string;
  source_id: string;
  /** Resolved routing hint; never serialized to the API or trusted from query parameters. */
  source_slug?: string;
  tag: string;
  cursor: string;
  sort?: string;
};
const first = (value: string | string[] | undefined) =>
  (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
export function parseFilters(params: SearchParams): FeedFilters {
  const type = first(params.content_type);
  const sort = first(params.sort);
  const source = first(params.source_id);
  return {
    q: first(params.q).slice(0, 200),
    topic: first(params.topic).slice(0, 100),
    content_type: contentTypes.some((value) => value === type) ? type : "",
    sort: ["newest", "oldest", "most_liked", "recommended"].includes(sort) ? sort : "",
    source_id: /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(source) ? source : "",
    tag: first(params.tag).slice(0, 100),
    cursor: first(params.cursor).slice(0, 300),
  };
}
export function feedParams(filters: FeedFilters): URLSearchParams {
  return new URLSearchParams(
    Object.entries(filters).filter(
      ([key, value]) =>
        key !== "source_slug" && key !== "language" && value !== "" && value !== undefined,
    ) as [string, string][],
  );
}
export function latestFeedParams(filters: FeedFilters): URLSearchParams {
  const params = feedParams(filters);
  if (params.get("sort") === "recommended") params.delete("sort");
  if (!filters.sort && !filters.q && !filters.source_id && !filters.topic && !filters.tag)
    params.set("diverse", "true");
  return params;
}
export function personalFeedHref(filters: FeedFilters, changes: Partial<FeedFilters> = {}) {
  const params = feedParams({ ...filters, cursor: "", ...changes });
  return params.size ? `/?${params}` : "/";
}
export function sourceHref(source: { id: string; slug?: string }) {
  return `/sources/${encodeURIComponent(source.slug || source.id)}`;
}
export function feedHref(filters: FeedFilters, changes: Partial<FeedFilters> = {}): string {
  const next = { ...filters, cursor: "", ...changes };
  if (
    changes.source_id !== undefined &&
    changes.source_id !== filters.source_id &&
    changes.source_slug === undefined
  )
    next.source_slug = undefined;
  const params = feedParams(next);
  let path = "/latest";
  if (next.topic) {
    path = `/topics/${encodeURIComponent(next.topic)}`;
    params.delete("topic");
  } else if (next.source_id) {
    path = sourceHref({ id: next.source_id, slug: next.source_slug });
    params.delete("source_id");
  } else if (next.tag) {
    path = `/tags/${encodeURIComponent(next.tag)}`;
    params.delete("tag");
  }
  const type = contentTypes.find((type) => type === next.content_type);
  if (type) {
    path = `${path === "/latest" ? "" : path}/${contentTypeRoutes[type]}`;
    params.delete("content_type");
  }
  return params.size ? `${path}?${params}` : path;
}
export function safeExternalUrl(value: string | null | undefined): string | undefined {
  try {
    const url = new URL(value ?? "");
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function outboundArticleUrl(value: string): string | undefined {
  const safe = safeExternalUrl(value);
  if (!safe) return undefined;
  const url = new URL(safe);
  url.searchParams.set("utm_source", "devfeed");
  return url.href;
}
export function displayHost(value: string): string {
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return "Original publisher";
  }
}
export function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : new Intl.DateTimeFormat("en", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      }).format(date);
}
