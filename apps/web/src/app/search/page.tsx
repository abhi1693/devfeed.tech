import { SuspenseReveal } from "@/components/suspense-reveal";
import { LoadingSkeleton } from "@/components/loading-skeleton";
import { canonicalUrl, pageMetadata } from "@/lib/metadata";
import { UserShell } from "@/components/user-shell";
import { SearchFailure, SearchResults } from "@/components/search-results";
import { getSearch } from "@/lib/api";
import { SearchFilters } from "@/components/search-filters";
import {
  normalizeSearch,
  parseSearchOptions,
  searchOptionParams,
  type SearchOptions,
} from "@/lib/search";
export const dynamic = "force-dynamic";
export function generateMetadata() {
  return {
    ...pageMetadata(
      "Search",
      "Find articles, topics, sources, and tags in one place.",
      canonicalUrl("/search"),
    ),
    robots: { index: false, follow: true },
  };
}
async function Results({ query, options }: { query: string; options: SearchOptions }) {
  const result = await getSearch(
    query,
    options.section || undefined,
    "1",
    undefined,
    options,
  ).catch(() => null);
  return result ? (
    <SearchResults key={query} result={result} options={options} />
  ) : (
    <SearchFailure />
  );
}
export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = normalizeSearch(typeof params.q === "string" ? params.q : (params.q?.[0] ?? ""));
  const options = parseSearchOptions(
    new URLSearchParams(
      Object.entries(params).flatMap(([key, value]) =>
        value === undefined ? [] : [[key, typeof value === "string" ? value : value[0]]],
      ),
    ),
  );
  const resultKey = `${query}:${searchOptionParams(options)}`;
  return (
    <UserShell section="search" searchQuery={query}>
      <div className="page-heading search-heading">
        <h1>{query ? `Results for “${query}”` : "Search DevFeed"}</h1>
        {!query && <p>Find articles, topics, sources, and tags in one place.</p>}
      </div>
      <SearchFilters key={`filters:${resultKey}`} query={query} options={options} />
      {query && (
        <SuspenseReveal
          key={`results:${resultKey}`}
          fallback={<LoadingSkeleton label="Searching DevFeed…" />}
        >
          <Results query={query} options={options} />
        </SuspenseReveal>
      )}
    </UserShell>
  );
}
