"use client";

import Link from "next/link";
import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { Search, ArrowUpRight } from "lucide-react";
import { RetryButton } from "@devfeed/ui/retry-button";
import { useInfinitePages } from "@/lib/use-infinite-pages";
import {
  searchKinds,
  type SearchKind,
  type SearchResponse,
  type SearchSection as Section,
} from "@/lib/search";
import { InfiniteScroll } from "./infinite-scroll";
import { UserDate } from "./user-date";
import { CatalogIcon } from "./catalog-icon";

function ResultSection({
  kind,
  query,
  initial,
}: {
  kind: SearchKind;
  query: string;
  initial: Section;
}) {
  const fetchPage = useCallback(
    async (page: string, signal: AbortSignal) => {
      const response = await fetch(
        `/api/v1/search?${new URLSearchParams({ q: query, section: kind, page })}`,
        {
          cache: "no-store",
          signal: AbortSignal.any([signal, AbortSignal.timeout(3500)]),
        },
      );
      if (!response.ok) throw new Error("Search unavailable");
      const result = (await response.json()) as SearchResponse;
      if (!result.sections[kind]) throw new Error("Invalid search response");
      return result.sections[kind];
    },
    [kind, query],
  );
  const { pages, cursor, loading, error, loadMore } = useInfinitePages(initial, fetchPage);
  const ids = new Set<string>();
  const items = pages
    .flatMap((page) => page.items)
    .filter((item) => !ids.has(item.id) && !!ids.add(item.id));
  const title = kind[0].toUpperCase() + kind.slice(1);
  return (
    <section className={`search-section search-section-${kind}`} aria-labelledby={`search-${kind}`}>
      <h2 id={`search-${kind}`}>{title}</h2>
      <InfiniteScroll
        autoLoad={false}
        hasMore={cursor !== null}
        loading={loading}
        error={!!error}
        onLoadMore={loadMore}
        label={kind}
        endMessage=""
      >
        <div className="search-result-list">
          {items.map((item) => (
            <article key={item.id} className="search-result">
              {kind !== "articles" && kind !== "tags" && (
                <CatalogIcon url={item.image_url} source={kind === "sources"} />
              )}
              <div className="search-result-copy">
                <h3>
                  <Link
                    href={item.href}
                    prefetch={false}
                    scroll={kind === "articles" ? false : undefined}
                  >
                    {kind === "tags" && <span aria-hidden="true">#</span>}
                    {item.title}
                  </Link>
                </h3>
                {item.description && kind !== "tags" && <p>{item.description}</p>}
                {kind === "articles" && (
                  <div className="search-result-meta">
                    <span>{item.label}</span>
                    {item.published_at && <UserDate value={item.published_at} />}
                  </div>
                )}
              </div>
              {kind !== "articles" && <ArrowUpRight size={15} aria-hidden="true" />}
            </article>
          ))}
          {!items.length && <p className="search-no-section">No matching {kind}.</p>}
        </div>
      </InfiniteScroll>
    </section>
  );
}

export function SearchResults({ result }: { result: SearchResponse }) {
  if (
    !searchKinds.some(
      (kind) => result.sections[kind]?.items.length || result.sections[kind]?.next_cursor,
    )
  )
    return (
      <div className="empty-state" role="status">
        <Search size={32} aria-hidden="true" />
        <h2>No results for “{result.query}”</h2>
        <p>Try a different term or a shorter query.</p>
      </div>
    );
  return (
    <div className="search-results-layout">
      <ResultSection
        kind="articles"
        query={result.query}
        initial={result.sections.articles ?? { items: [], next_cursor: null }}
      />
      <div className="search-related-results">
        {searchKinds
          .filter((kind) => kind !== "articles")
          .map((kind) => (
            <ResultSection
              key={kind}
              kind={kind}
              query={result.query}
              initial={result.sections[kind] ?? { items: [], next_cursor: null }}
            />
          ))}
      </div>
    </div>
  );
}

export function SearchFailure() {
  const router = useRouter();
  return (
    <div className="empty-state" role="alert">
      <h2>Search is temporarily unavailable</h2>
      <p>Please try again in a moment.</p>
      <RetryButton onRetry={() => router.refresh()} />
    </div>
  );
}
