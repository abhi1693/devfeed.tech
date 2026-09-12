import { Suspense } from "react";
import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { SearchFailure, SearchResults } from "@/components/search-results";
import { getSearch } from "@/lib/api";
import { normalizeSearch } from "@/lib/search";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Search", robots: { index: false, follow: true } };
async function Results({ query }: { query: string }) {
  const result = await getSearch(query).catch(() => null);
  return result ? <SearchResults key={query} result={result} /> : <SearchFailure />;
}
export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string | string[] }>;
}) {
  const params = await searchParams;
  const query = normalizeSearch(typeof params.q === "string" ? params.q : (params.q?.[0] ?? ""));
  return (
    <UserShell section="search" searchQuery={query}>
      <div className="page-heading search-heading">
        <h1>{query ? `Results for “${query}”` : "Search DevFeed"}</h1>
        {!query && <p>Find articles, topics, sources, and tags in one place.</p>}
      </div>
      {query && (
        <Suspense
          key={query}
          fallback={
            <div className="search-loading" role="status" aria-live="polite">
              Searching DevFeed…
            </div>
          }
        >
          <Results query={query} />
        </Suspense>
      )}
    </UserShell>
  );
}
