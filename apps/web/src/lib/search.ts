export const searchKinds = ["articles", "topics", "sources", "tags"] as const;
export type SearchKind = (typeof searchKinds)[number];
export type SearchHit = {
  id: string;
  title: string;
  description: string;
  href: string;
  image_url: string | null;
  label: string;
  published_at: string | null;
};
export type SearchSection = { items: SearchHit[]; next_cursor: string | null };
export type SearchResponse = {
  query: string;
  sections: Partial<Record<SearchKind, SearchSection>>;
};
export function normalizeSearch(query: string) {
  return query.normalize("NFKC").replace(/\s+/g, " ").trim().slice(0, 200);
}
export function searchHref(query: string) {
  const q = normalizeSearch(query);
  return q ? `/search?${new URLSearchParams({ q })}` : "/search";
}

export type SearchOptions = {
  section: SearchKind | "";
  sort: "relevance" | "newest" | "oldest";
  date_from: string;
  date_to: string;
};
export function parseSearchOptions(params: URLSearchParams): SearchOptions {
  const section = params.get("section") ?? "";
  const sort = params.get("sort") ?? "relevance";
  const date = (key: string) => {
    const value = params.get(key) ?? "";
    const parsed = new Date(`${value}T00:00:00Z`);
    return /^\d{4}-\d{2}-\d{2}$/.test(value) &&
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
      ? value
      : "";
  };
  return {
    section: searchKinds.includes(section as SearchKind) ? (section as SearchKind) : "",
    sort: sort === "newest" || sort === "oldest" ? sort : "relevance",
    date_from: date("date_from"),
    date_to: date("date_to"),
  };
}
export function searchOptionParams(options?: SearchOptions) {
  const params = new URLSearchParams();
  if (!options) return params;
  if (options.section) params.set("section", options.section);
  if (options.sort !== "relevance") params.set("sort", options.sort);
  if (options.date_from) params.set("date_from", options.date_from);
  if (options.date_to) params.set("date_to", options.date_to);
  return params;
}
