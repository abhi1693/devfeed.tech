"use client";

import { VideoMetadata } from "./video-metadata";

import "../app/styles/discovery.css";

import Link from "@/components/reader-link";
import { readerRequest } from "@/lib/reader-runtime";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useReaderRouter } from "@/lib/reader-navigation";
import { Search, ArrowUpRight } from "lucide-react";
import { RetryButton } from "@devfeed/ui/retry-button";
import { safeExternalUrl } from "@/lib/feed-query";
import { ArticleImage } from "./article-image";
import { flattenPageItems, uniquePageItems, useInfinitePages } from "@/lib/use-infinite-pages";
import {
  searchKinds,
  searchOptionParams,
  normalizeSearch,
  type SearchOptions,
  type SearchKind,
  type SearchResponse,
  type SearchSection as Section,
} from "@/lib/search";
import { InfiniteScroll } from "./infinite-scroll";
import { UserDate } from "./user-date";
import { LoadingSkeleton } from "./loading-skeleton";
import { CatalogIcon } from "./catalog-icon";
import { TopicFollow } from "./topic-follow";
import { SourceFollow } from "./source-follow";
import { trackEvent } from "@/lib/analytics";
import { PartnerPlacements } from "./partner-placements";
import { useReadingAnchor } from "./use-reading-anchor";
import { ArticleSponsorship } from "./article-sponsorship";
import { SEARCH_PLACEMENT_RESULT_LIMIT, type PartnerPlacement } from "@/lib/partner-placements";
import { adLinkRel } from "@devfeed/ui/ad-link-rel";

function ResultSection({
  kind,
  query,
  initial,
  options,
  onResults,
}: {
  kind: SearchKind;
  query: string;
  initial: Section;
  options?: SearchOptions;
  onResults: (kind: SearchKind, ids: string[]) => void;
}) {
  const optionQuery = searchOptionParams(options).toString();
  const returnParams = new URLSearchParams(optionQuery);
  returnParams.set("q", query);
  const returnTo = `/search?${returnParams}`;
  const fetchPage = useCallback(
    async (page: string, signal: AbortSignal) => {
      const params = new URLSearchParams(optionQuery);
      params.set("q", query);
      params.set("section", kind);
      params.set("page", page);
      const response = await readerRequest(`/api/v1/search?${params}`, {
        cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(3500)]),
      });
      if (!response.ok) throw new Error("Search unavailable");
      const result = (await response.json()) as SearchResponse;
      if (!result.sections[kind]) throw new Error("Invalid search response");
      return result.sections[kind];
    },
    [kind, query, optionQuery],
  );
  const { pages, cursor, loading, error, loadMore } = useInfinitePages(initial, fetchPage);
  const items = useMemo(() => uniquePageItems(flattenPageItems(pages), (item) => item.id), [pages]);
  useEffect(
    () =>
      onResults(
        kind,
        items.map((item) => item.id),
      ),
    [kind, items, onResults],
  );
  const impressionIds = useRef(new Set<string>());
  useEffect(() => {
    items.forEach((item, index) => {
      const key = `${kind}:${item.id}`;
      if (impressionIds.current.has(key)) return;
      impressionIds.current.add(key);
      trackEvent("search_impression", {
        result_kind: kind,
        result_id: item.id,
        position: index + 1,
      });
    });
  }, [items, kind]);
  const list = (
    <div className="search-result-list">
      {items.map((item, index) => (
        <article key={item.id} className="search-result">
          {kind !== "articles" && kind !== "tags" && (
            <CatalogIcon
              url={item.image_url}
              variants={item.logo_variants}
              source={kind === "sources"}
            />
          )}
          <div className="search-result-copy">
            <div className="search-result-heading">
              <h3>
                <Link
                  className="search-result-link"
                  href={item.href}
                  prefetch={false}
                  scroll={kind === "articles" ? false : undefined}
                  rel={
                    item.sponsor_names?.length
                      ? adLinkRel(item.sponsor_link_rel, false) || undefined
                      : undefined
                  }
                  onClick={() => {
                    const position = index + 1;
                    trackEvent("search_click", {
                      result_kind: kind,
                      result_id: item.id,
                      position,
                    });
                    trackEvent("search_conversion", {
                      result_kind: kind,
                      result_id: item.id,
                      position,
                    });
                    void Promise.resolve(
                      readerRequest("/api/v1/search/analytics/click", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                          query,
                          result_kind: kind,
                          result_id: item.id,
                          click_token: item.click_token,
                        }),
                        keepalive: true,
                      }),
                    ).catch(() => undefined);
                  }}
                >
                  {kind === "tags" && <span aria-hidden="true">#</span>}
                  {item.title}
                </Link>
              </h3>
              {(kind === "topics" || kind === "sources") && (
                <div
                  className="search-result-actions"
                  role="group"
                  aria-label={`Follow ${item.title}`}
                >
                  {kind === "topics" ? (
                    <TopicFollow topicId={item.id} returnTo={returnTo} />
                  ) : (
                    <SourceFollow sourceId={item.id} returnTo={returnTo} compact />
                  )}
                </div>
              )}
            </div>
            {item.description && kind !== "tags" && <p>{item.description}</p>}
            {kind === "articles" && (
              <>
                <VideoMetadata content={item} />
                <div className="search-result-meta">
                  <span className="content-type" data-content-type={item.label}>
                    {item.label}
                  </span>
                  {item.published_at && <UserDate value={item.published_at} />}
                  <ArticleSponsorship names={item.sponsor_names} />
                </div>
              </>
            )}
          </div>
          {kind === "articles" && (
            <div className="search-result-thumbnail" aria-hidden="true">
              <ArticleImage
                src={safeExternalUrl(item.image_url) ?? undefined}
                label=""
                sizes="(max-width: 520px) 88px, 144px"
              />
            </div>
          )}
          {kind === "tags" && <ArrowUpRight size={15} aria-hidden="true" />}
        </article>
      ))}
      {!items.length && <p className="search-no-section">No matching {kind}.</p>}
    </div>
  );
  const title = kind[0].toUpperCase() + kind.slice(1);
  return (
    <section className={`search-section search-section-${kind}`} aria-labelledby={`search-${kind}`}>
      <h2 id={`search-${kind}`} className={kind === "articles" ? "sr-only" : undefined}>
        {title}
      </h2>
      <InfiniteScroll
        hasMore={cursor !== null}
        loading={loading}
        error={!!error}
        onLoadMore={loadMore}
        label={kind}
        endMessage=""
      >
        {list}
      </InfiniteScroll>
    </section>
  );
}

export function SearchResults({
  result,
  options,
}: {
  result: SearchResponse;
  options?: SearchOptions;
}) {
  return (
    <SearchResultsPage
      key={`${result.query}:${searchOptionParams(options)}`}
      result={result}
      options={options}
    />
  );
}

function SearchResultsPage({
  result,
  options,
}: {
  result: SearchResponse;
  options?: SearchOptions;
}) {
  const optionKey = searchOptionParams(options).toString();
  const { ref, beforeDisplay, afterDisplay } = useReadingAnchor(".search-result");
  const initialContext = searchKinds
    .filter((kind) => !options?.section || options.section === kind)
    .flatMap((kind) => (result.sections[kind]?.items ?? []).map((item) => ({ kind, id: item.id })));
  const seen = useRef(new Set(initialContext.map(({ kind, id }) => `${kind}:${id}`)));
  const delivered = useRef(false);
  const [context, setContext] = useState(() =>
    initialContext
      .filter(
        ({ kind, id }, index, entries) =>
          entries.findIndex((entry) => entry.kind === kind && entry.id === id) === index,
      )
      .slice(-SEARCH_PLACEMENT_RESULT_LIMIT),
  );
  const observed = useRef(context);
  const recordResults = useCallback((kind: SearchKind, ids: string[]) => {
    const added = ids
      .filter((id) => {
        const key = `${kind}:${id}`;
        if (seen.current.has(key)) return false;
        seen.current.add(key);
        return true;
      })
      .map((id) => ({ kind, id }));
    if (added.length) {
      observed.current = [...observed.current, ...added].slice(-SEARCH_PLACEMENT_RESULT_LIMIT);
      if (!delivered.current) setContext(observed.current);
    }
  }, []);
  const recordDelivery = useCallback((items: PartnerPlacement[]) => {
    delivered.current = items.length > 0;
    if (!delivered.current) setContext(observed.current);
  }, []);
  const showArticles = !options?.section || options.section === "articles";
  const showCatalog = options?.section !== "articles";
  const section = options?.section || "all";
  const resultCount = searchKinds.reduce(
    (total, kind) => total + (result.sections[kind]?.items.length ?? 0),
    0,
  );
  useEffect(() => {
    const wordCount = result.query ? result.query.split(/\s+/).length : 0;
    trackEvent("search_query", {
      query_length: result.query.length,
      word_count: wordCount,
      section,
      result_count: resultCount,
    });
    if (!resultCount) {
      trackEvent("search_zero_result", {
        query_length: result.query.length,
        word_count: wordCount,
        section,
      });
    }
  }, [optionKey, result.query, resultCount, section]);
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
  const catalog = showCatalog && (
    <div className="search-related-results">
      {searchKinds
        .filter((kind) => kind !== "articles" && (!options?.section || options.section === kind))
        .map((kind) => (
          <ResultSection
            options={options}
            key={`${kind}:${result.query}:${optionKey}`}
            kind={kind}
            query={result.query}
            initial={result.sections[kind] ?? { items: [], next_cursor: null }}
            onResults={recordResults}
          />
        ))}
    </div>
  );
  const results = (cards: ReactNode[] = []) => (
    <div
      ref={ref}
      className={`search-results-layout${options?.section && !cards.length ? " search-results-filtered" : ""}`}
    >
      {showArticles && (
        <ResultSection
          options={options}
          key={`articles:${result.query}:${optionKey}`}
          kind="articles"
          query={result.query}
          initial={result.sections.articles ?? { items: [], next_cursor: null }}
          onResults={recordResults}
        />
      )}
      {!showArticles && catalog}
      {((showArticles && showCatalog) || cards.length > 0) && (
        <aside className="search-sidebar" aria-label="Related to your search">
          {cards.length > 0 && (
            <section
              className="partner-placements search-engagement"
              aria-label="Sponsored product"
            >
              {cards}
            </section>
          )}
          {showArticles && catalog}
        </aside>
      )}
    </div>
  );
  return (
    <PartnerPlacements
      key={`${result.query}:${optionKey}`}
      surface="search"
      search={{
        query: normalizeSearch(result.query),
        results: Object.fromEntries(
          searchKinds
            .map((kind) => [
              kind,
              context.filter((entry) => entry.kind === kind).map(({ id }) => id),
            ])
            .filter(([, ids]) => ids.length > 0),
        ),
      }}
      ad_formats={["engagement"]}
      maxItems={context.length ? 1 : 0}
      beforeDisplay={beforeDisplay}
      afterDisplay={afterDisplay}
      onDelivery={recordDelivery}
    >
      {results}
    </PartnerPlacements>
  );
}

export function SearchFailure() {
  const router = useReaderRouter();
  return (
    <div className="empty-state" role="alert">
      <h2>Search is temporarily unavailable</h2>
      <p>Please try again in a moment.</p>
      <RetryButton onRetry={() => router.refresh()} />
    </div>
  );
}

export function SearchLoading() {
  return (
    <div aria-busy="true">
      <p>Searching DevFeed…</p>
      <LoadingSkeleton label="Searching DevFeed…" />
    </div>
  );
}
