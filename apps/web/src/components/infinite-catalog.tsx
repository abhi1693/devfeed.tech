"use client";
import { sourceHref } from "@/lib/feed-query";

import Link from "next/link";
import { useCallback } from "react";
import { Markdown } from "@devfeed/ui/markdown";
import { AccountError } from "@/lib/user";
import { catalogPage } from "@/lib/catalog-page";
import { useInfinitePages } from "@/lib/use-infinite-pages";
import type { Source, Topic } from "@/lib/types";
import { CatalogIcon } from "./catalog-icon";
import { SourceFollow } from "./source-follow";
import { InfiniteScroll } from "./infinite-scroll";

type Item = Source | Topic;
export function InfiniteCatalog({
  kind,
  initialItems,
  offset,
}: {
  kind: "topics" | "sources";
  initialItems: Item[];
  offset: number;
}) {
  const fetchPage = useCallback(
    async (cursor: string, signal: AbortSignal) => {
      const response = await fetch(`/api/v1/${kind}?offset=${encodeURIComponent(cursor)}`, {
        signal,
        cache: "no-store",
      });
      if (!response.ok) throw new AccountError(response.status);
      return (await response.json()) as { items: Item[]; next_cursor: string | null };
    },
    [kind],
  );
  const { pages, cursor, loading, error, loadMore } = useInfinitePages(
    catalogPage(initialItems, offset),
    fetchPage,
  );
  const ids = new Set<string>();
  const items = pages
    .flatMap((page) => page.items)
    .filter((item) => {
      if (ids.has(item.id)) return false;
      ids.add(item.id);
      return true;
    });
  return (
    <InfiniteScroll
      hasMore={cursor !== null}
      loading={loading}
      error={!!error}
      onLoadMore={loadMore}
      label={kind}
      nextHref={cursor !== null ? `/${kind}?offset=${cursor}` : undefined}
      endMessage={`You’ve seen all ${kind}.`}
    >
      <div className="topic-grid">
        {items.map((item) =>
          "kind" in item ? (
            <Link
              key={item.id}
              href={`/topics/${encodeURIComponent(item.slug)}`}
              className="topic-card catalog-card"
            >
              <div className="topic-card-heading">
                <CatalogIcon url={item.logo_url} />
                <h2>{item.name}</h2>
              </div>
              {(item.description || item.ai_description) && (
                <p>{item.description || item.ai_description}</p>
              )}
            </Link>
          ) : (
            <article key={item.id} className="topic-card catalog-card source-card">
              <Link href={sourceHref(item)} className="source-card-link">
                <div className="topic-card-heading">
                  <CatalogIcon url={item.logo_url} source />
                  <h2>{item.name}</h2>
                </div>
              </Link>
              {item.description && <Markdown compact>{item.description}</Markdown>}
              <SourceFollow sourceId={item.id} returnTo={sourceHref(item)} />
            </article>
          ),
        )}
      </div>
    </InfiniteScroll>
  );
}
