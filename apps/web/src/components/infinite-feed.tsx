"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { feedHref, feedParams, type FeedFilters } from "@/lib/feed-query";
import type { FeedPage } from "@/lib/types";
import { AccountError, userRequest } from "@/lib/user";
import { ArticleGrid, type RecommendationReason } from "./article-grid";

type Page = FeedPage & {
  reasons?: Record<string, RecommendationReason>;
  status?: "ready" | "refreshing";
};
type Props = {
  initialPage: Page;
} & ({ filters: FeedFilters; personal?: false } | { personal: true; filters?: never });

export function InfiniteFeed({ initialPage, filters, personal = false }: Props) {
  const [pages, setPages] = useState([initialPage]);
  const [cursor, setCursor] = useState(initialPage.next_cursor);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<"retry" | "changed" | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);
  const loaded = useRef(new Set<string>());
  useEffect(() => () => {
    request.current?.abort();
    request.current = null;
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor || request.current || loaded.current.has(cursor)) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    try {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]);
      let page: Page;
      if (personal) {
        page = await userRequest<Page>(`feed?limit=24&cursor=${encodeURIComponent(cursor)}`, { signal });
      } else {
        const params = feedParams({ ...filters!, cursor });
        const response = await fetch(`/api/v1/feed?${params}`, { signal, cache: "no-store" });
        if (!response.ok) throw new AccountError(response.status);
        page = await response.json() as Page;
      }
      if (controller.signal.aborted) return;
      if (page.status === "refreshing") throw new AccountError(409);
      loaded.current.add(cursor);
      setPages(current => {
        const ids = new Set(current.flatMap(batch => batch.items.map(item => item.id)));
        const items = page.items.filter(item => {
          if (ids.has(item.id)) return false;
          ids.add(item.id);
          return true;
        });
        return [...current, { ...page, items }];
      });
      setCursor(page.next_cursor && !loaded.current.has(page.next_cursor) ? page.next_cursor : null);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(personal && cause instanceof AccountError && cause.status === 409 ? "changed" : "retry");
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setLoading(false);
      }
    }
  }, [cursor, filters, personal]);

  useEffect(() => {
    if (!cursor || loading || error || !sentinel.current || !globalThis.IntersectionObserver) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) void loadMore();
    }, { rootMargin: "600px 0px" });
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [cursor, loading, error, loadMore]);

  const nextHref = cursor ? personal
    ? `/my-feed?cursor=${encodeURIComponent(cursor)}`
    : feedHref(filters!, { cursor }) : undefined;
  return <>
    <div className="feed-pages">
      {pages.map((page, index) => <ArticleGrid key={index} articles={page.items} reasons={page.reasons} priority={index === 0} />)}
    </div>
    <div ref={sentinel} className="pagination" aria-busy={loading}>
      <p role="status">{loading ? "Loading more articles…" : error === "changed" ? "Your feed has been updated." : error ? "Couldn’t load more articles." : !cursor ? "You’re all caught up." : ""}</p>
      {error === "changed" ? (
        // eslint-disable-next-line @next/next/no-html-link-for-pages -- Reload the current recommendation generation.
        <a className="button" href="/my-feed">Show updated feed</a>
      ) : nextHref && !loading ? <Link className="button" href={nextHref} prefetch={false} onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        void loadMore();
      }}>{error ? "Try again" : "More articles"}</Link> : null}
    </div>
  </>;
}
