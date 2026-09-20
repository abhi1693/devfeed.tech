"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isPageActive, runWhenPageActive } from "@devfeed/ui/page-activity";

type Page = { next_cursor: string | null };
type ItemsPage<T> = { items: T[] };

/** Flattens page data without imposing a fetch or cursor policy. */
export function flattenPageItems<T>(pages: readonly ItemsPage<T>[]) {
  return pages.flatMap((page) => page.items);
}

/** Removes duplicates while retaining either the first or latest item for each key. */
export function uniquePageItems<T, Key>(
  items: readonly T[],
  key: (item: T) => Key,
  keep: "first" | "last" = "first",
) {
  if (keep === "last") return Array.from(new Map(items.map((item) => [key(item), item])).values());
  const seen = new Set<Key>();
  return items.filter((item) => {
    const itemKey = key(item);
    if (seen.has(itemKey)) return false;
    seen.add(itemKey);
    return true;
  });
}

/** Preserves page grouping while excluding and deduplicating items across all pages. */
export function filterUniquePageItems<P extends ItemsPage<unknown>>(
  pages: readonly P[],
  key: (item: P["items"][number]) => string,
  include: (item: P["items"][number]) => boolean = () => true,
) {
  const seen = new Set<string>();
  return pages.map(
    (page) =>
      ({
        ...page,
        items: page.items.filter((item) => {
          const itemKey = key(item);
          if (!include(item) || seen.has(itemKey)) return false;
          seen.add(itemKey);
          return true;
        }),
      }) as P,
  );
}

/** One cancellable request per cursor; callers retain their own page shape. */
export function useInfinitePages<T extends Page>(
  initialPage: T,
  fetchPage: (cursor: string, signal: AbortSignal) => Promise<T>,
) {
  const [pages, setPages] = useState([initialPage]);
  const [cursor, setCursor] = useState(initialPage.next_cursor);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const request = useRef<AbortController | null>(null);
  const loaded = useRef(new Set<string>());

  useEffect(
    () =>
      runWhenPageActive(() => () => {
        request.current?.abort();
        request.current = null;
        setLoading(false);
      }),
    [],
  );

  const loadMore = useCallback(async () => {
    if (!isPageActive() || cursor === null || request.current || loaded.current.has(cursor)) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchPage(
        cursor,
        AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
      );
      if (controller.signal.aborted) return;
      loaded.current.add(cursor);
      setPages((current) => [...current, page]);
      setCursor(
        page.next_cursor !== null && !loaded.current.has(page.next_cursor)
          ? page.next_cursor
          : null,
      );
      return page;
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause : new Error("Could not load page"));
    } finally {
      if (request.current === controller) {
        request.current = null;
        setLoading(false);
      }
    }
  }, [cursor, fetchPage]);

  return { pages, cursor, loading, error, loadMore };
}
