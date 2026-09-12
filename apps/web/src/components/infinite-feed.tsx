"use client";

import { useArticleNavigation } from "./article-navigation";
import { useCallback, useEffect, useMemo } from "react";
import { feedHref, feedParams, type FeedFilters } from "@/lib/feed-query";
import type { FeedPage } from "@/lib/types";
import { AccountError, userRequest } from "@/lib/user";
import { useInfinitePages } from "@/lib/use-infinite-pages";
import { InfiniteScroll } from "./infinite-scroll";
import { ArticleGrid, type RecommendationReason } from "./article-grid";

type Page = FeedPage & {
  reasons?: Record<string, RecommendationReason>;
  status?: "ready" | "refreshing";
};
type Props = {
  initialPage: Page;
} & ({ filters: FeedFilters; personal?: false; trending?: false } | { personal: true; filters?: never; trending?: false } | { trending: true; personal?: false; filters?: never });

export function InfiniteFeed({ initialPage, filters, personal = false, trending = false }: Props) {
  const { setSequence } = useArticleNavigation();
  const fetchPage = useCallback(async (cursor: string, signal: AbortSignal): Promise<Page> => {
    let page: Page;
    if (personal || trending) {
      page = await userRequest<Page>(`${trending ? "trending" : "feed"}?limit=24&cursor=${encodeURIComponent(cursor)}`, { signal });
    } else {
      const response = await fetch(`/api/v1/feed?${feedParams({ ...filters!, cursor })}`, { signal, cache: "no-store" });
      if (!response.ok) throw new AccountError(response.status);
      page = await response.json() as Page;
    }
    if (page.status === "refreshing") throw new AccountError(409);
    return page;
  }, [filters, personal, trending]);
  const { pages: batches, cursor, loading, error, loadMore: loadPage } = useInfinitePages(initialPage, fetchPage);
  const pages = useMemo(() => {
    const ids = new Set<string>();
    return batches.map(page => ({ ...page, items: page.items.filter(item => {
      if (ids.has(item.id)) return false;
      ids.add(item.id);
      return true;
    }) }));
  }, [batches]);
  const loadMore = useCallback(async () => {
    const page = await loadPage();
    const visible = new Set(pages.flatMap(batch => batch.items.map(item => item.id)));
    return page?.items.find(item => !visible.has(item.id))?.slug;
  }, [loadPage, pages]);
  const changed = personal && error instanceof AccountError && error.status === 409;

  useEffect(() => {
    setSequence({ slugs: pages.flatMap(page => page.items.map(article => article.slug)), hasMore: cursor !== null, loading, loadMore });
  }, [pages, cursor, loading, loadMore, setSequence]);
  useEffect(() => () => setSequence(null), [setSequence]);

  const nextHref = cursor !== null ? personal || trending
    ? `/${trending ? "trending" : "my-feed"}?cursor=${encodeURIComponent(cursor)}`
    : feedHref(filters!, { cursor }) : undefined;
  return <InfiniteScroll hasMore={cursor !== null} loading={loading} error={!!error} onLoadMore={loadMore} label="articles" nextHref={nextHref} errorMessage={changed ? "Your feed has been updated." : undefined} recovery={changed ? (
    // eslint-disable-next-line @next/next/no-html-link-for-pages -- Reload the current recommendation generation.
    <a className="button" href="/my-feed">Show updated feed</a>
  ) : undefined}>
    <div className="feed-pages">
      {pages.map((page, index) => <ArticleGrid key={index} articles={page.items} reasons={page.reasons} priority={index === 0} />)}
    </div>
  </InfiniteScroll>;
}
