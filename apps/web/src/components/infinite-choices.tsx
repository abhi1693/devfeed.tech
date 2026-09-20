"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { catalogPage } from "@/lib/catalog-page";
import { readerRequest } from "@/lib/reader-runtime";
import { flattenPageItems, uniquePageItems, useInfinitePages } from "@/lib/use-infinite-pages";
import { InfiniteScroll } from "./infinite-scroll";

/** Fetch one catalog page at a time; search is applied before database pagination. */
export function InfiniteChoices<T extends { id: string; name: string }>({
  query = "",
  ...props
}: {
  items?: T[];
  label: "topics" | "sources";
  query?: string;
  sort?: "name" | "articles";
  children: (visible: T[], complete: boolean) => ReactNode;
}) {
  const [search, setSearch] = useState(query.trim().slice(0, 200));
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim().slice(0, 200)), 200);
    return () => clearTimeout(timer);
  }, [query]);
  return <ChoicePages key={`${props.label}:${props.sort}:${search}`} {...props} query={search} />;
}
function ChoicePages<T extends { id: string; name: string }>({
  items,
  label,
  query,
  sort = "name",
  children,
}: {
  items?: T[];
  label: "topics" | "sources";
  query: string;
  sort?: "name" | "articles";
  children: (visible: T[], complete: boolean) => ReactNode;
}) {
  const fetchPage = useCallback(
    async (cursor: string, signal: AbortSignal) => {
      const params = new URLSearchParams({ offset: cursor, q: query, sort });
      const response = await readerRequest(`/api/v1/${label}?${params}`, { signal });
      if (!response.ok) throw new Error(`Couldn’t load ${label}`);
      return (await response.json()) as { items: T[]; next_cursor: string | null };
    },
    [label, query, sort],
  );
  const initial = items && !query ? catalogPage(items, 0) : { items: [] as T[], next_cursor: "0" };
  const { pages, cursor, loading, error, loadMore } = useInfinitePages(initial, fetchPage);
  useEffect(() => {
    if (pages.length === 1 && !initial.items.length) void loadMore();
  }, [pages.length, initial.items.length, loadMore]);
  const visible = uniquePageItems(flattenPageItems(pages), (item) => item.id, "last");
  return (
    <InfiniteScroll
      prefetchDistance={0}
      hasMore={cursor !== null}
      loading={loading}
      error={!!error}
      onLoadMore={loadMore}
      label={label}
      errorMessage={visible.length ? undefined : `Couldn’t load ${label}.`}
      endMessage=""
    >
      {children(visible, cursor === null)}
      {!visible.length && cursor === null && !error && <p>No {label} match your search.</p>}
    </InfiniteScroll>
  );
}
