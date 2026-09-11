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
  language: string;
  source_id: string;
  tag: string;
  cursor: string;
};
const first = (value: string | string[] | undefined) =>
  (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
export function parseFilters(params: SearchParams): FeedFilters {
  const type = first(params.content_type);
  const language = first(params.language);
  const source = first(params.source_id);
  return {
    q: first(params.q).slice(0, 200),
    topic: first(params.topic).slice(0, 100),
    content_type: contentTypes.some((value) => value === type) ? type : "",
    language:
      /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/.test(language) && language.length <= 35
        ? language
        : "",
    source_id: /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(source)
      ? source
      : "",
    tag: first(params.tag).slice(0, 100),
    cursor: first(params.cursor).slice(0, 300),
  };
}
export function feedParams(filters: FeedFilters): URLSearchParams {
  return new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value !== ""),
  );
}
export function feedHref(
  filters: FeedFilters,
  changes: Partial<FeedFilters> = {},
): string {
  const next = { ...filters, cursor: "", ...changes };
  const params = feedParams(next);
  let path = "/";
  if (next.topic) {
    path = `/topics/${encodeURIComponent(next.topic)}`;
    params.delete("topic");
  } else if (next.source_id) {
    path = `/sources/${encodeURIComponent(next.source_id)}`;
    params.delete("source_id");
  }
  const type = contentTypes.find((type) => type === next.content_type);
  if (type) {
    path = `${path === "/" ? "" : path}/${contentTypeRoutes[type]}`;
    params.delete("content_type");
  }
  return params.size ? `${path}?${params}` : path;
}
export function safeExternalUrl(
  value: string | null | undefined,
): string | undefined {
  try {
    const url = new URL(value ?? "");
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
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
