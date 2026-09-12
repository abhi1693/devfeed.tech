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
