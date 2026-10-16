import { contentTypes } from "./feed-query";
import type { AdLinkRel } from "@devfeed/ui/ad-link-rel";
import {
  readerDestinationContract,
  validReaderQueryValue,
} from "@devfeed/http/reader-destinations";

export type SearchKind = "articles" | "topics" | "sources" | "tags";
export const searchKinds = readerDestinationContract.queryFields.searchSection
  .values as readonly SearchKind[];
export type SearchHit = {
  id: string;
  title: string;
  description: string;
  href: string;
  image_url: string | null;
  logo_variants?: { url: string; width: number }[];
  label: string;
  published_at: string | null;
  click_token?: string | null;
  sponsor_names?: string[];
  sponsor_link_rel?: AdLinkRel[];
};
export type SearchSection = { items: SearchHit[]; next_cursor: string | null };
export type SearchResponse = {
  query: string;
  sections: Partial<Record<SearchKind, SearchSection>>;
};
export function normalizeSearch(query: string) {
  return query
    .normalize("NFKC")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, readerDestinationContract.queryFields.q.maxLength);
}
export function searchHref(query: string) {
  const q = normalizeSearch(query);
  return q ? `/search?${new URLSearchParams({ q })}` : "/search";
}

export type SearchOptions = {
  section: SearchKind | "";
  content_type?: (typeof contentTypes)[number];
  sort: "relevance" | "newest" | "oldest";
  date_from: string;
  date_to: string;
};
export function parseSearchOptions(params: URLSearchParams): SearchOptions {
  const section = params.get("section") ?? "";
  const contentType = contentTypes.find((type) => type === params.get("content_types"));
  const sort = params.get("sort") ?? "relevance";
  const date = (key: string) => {
    const value = params.get(key) ?? "";
    return validReaderQueryValue("date", value) ? value : "";
  };
  return {
    section: searchKinds.includes(section as SearchKind) ? (section as SearchKind) : "",
    ...(contentType && (!section || section === "articles")
      ? { section: "articles" as const, content_type: contentType }
      : {}),
    sort: validReaderQueryValue("searchSort", sort) ? (sort as SearchOptions["sort"]) : "relevance",
    date_from: date("date_from"),
    date_to: date("date_to"),
  };
}
export function searchOptionParams(options?: SearchOptions) {
  const params = new URLSearchParams();
  if (!options) return params;
  if (options.section) params.set("section", options.section);
  if (options.content_type) {
    params.set("section", "articles");
    params.set("content_types", options.content_type);
  }
  if (options.sort !== "relevance") params.set("sort", options.sort);
  if (options.date_from) params.set("date_from", options.date_from);
  if (options.date_to) params.set("date_to", options.date_to);
  return params;
}
