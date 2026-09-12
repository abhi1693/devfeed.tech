"use client";

import { useCallback, useState, type ReactNode } from "react";
import { CATALOG_PAGE_SIZE } from "@/lib/catalog-page";
import { InfiniteScroll } from "./infinite-scroll";

/** Preference forms keep their complete searchable catalog and saved selections. */
export function InfiniteChoices<T>({ items, label, children }: { items: T[]; label: string; children: (visible: T[]) => ReactNode }) {
  const [limit, setLimit] = useState(CATALOG_PAGE_SIZE);
  const loadMore = useCallback(async () => setLimit(current => current + CATALOG_PAGE_SIZE), []);
  return <InfiniteScroll hasMore={limit < items.length} loading={false} error={false} onLoadMore={loadMore} label={label} endMessage="">
    {children(items.slice(0, limit))}
  </InfiniteScroll>;
}
