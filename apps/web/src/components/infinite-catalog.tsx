"use client";
import { readerRequest } from "@/lib/reader-runtime";
import { sourceHref } from "@/lib/feed-query";

import { useCallback } from "react";
import { Markdown } from "@devfeed/ui/markdown";
import { AccountError } from "@/lib/user";
import { catalogPage } from "@/lib/catalog-page";
import { useInfinitePages } from "@/lib/use-infinite-pages";
import type { Source, Topic } from "@/lib/types";
import { SourceFollow } from "./source-follow";
import { TopicFollow } from "./topic-follow";
import { InfiniteScroll } from "./infinite-scroll";
import { CatalogCard } from "./catalog-card";

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
      const response = await readerRequest(`/api/v1/${kind}?offset=${encodeURIComponent(cursor)}`, {
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
            <CatalogCard
              key={item.id}
              href={`/topics/${encodeURIComponent(item.slug)}`}
              name={item.name}
              logoUrl={item.logo_url}
              description={
                (item.description || item.ai_description) && (
                  <p>{item.description || item.ai_description}</p>
                )
              }
              followAction={
                <TopicFollow
                  topicId={item.id}
                  returnTo={`/topics/${encodeURIComponent(item.slug)}`}
                />
              }
            />
          ) : (
            <CatalogCard
              key={item.id}
              href={sourceHref(item)}
              name={item.name}
              logoUrl={item.logo_url}
              source
              description={item.description && <Markdown compact>{item.description}</Markdown>}
              followAction={<SourceFollow sourceId={item.id} returnTo={sourceHref(item)} compact />}
            />
          ),
        )}
      </div>
    </InfiniteScroll>
  );
}
