"use client";
import { readerRequest } from "@/lib/reader-runtime";
import { sourceHref } from "@/lib/feed-query";

import { useCallback } from "react";
import { Markdown } from "@devfeed/ui/markdown";
import { AccountError } from "@/lib/user";
import { catalogHref, type CatalogPage } from "@/lib/catalog-page";
import { flattenPageItems, uniquePageItems, useInfinitePages } from "@/lib/use-infinite-pages";
import type { Source, Topic } from "@/lib/types";
import { SourceFollow } from "./source-follow";
import { TopicFollow } from "./topic-follow";
import { InfiniteScroll } from "./infinite-scroll";
import { CatalogCard } from "./catalog-card";
import Link from "./reader-link";

type Item = Source | Topic;
export function InfiniteCatalog({
  kind,
  initialPage,
  query = "",
}: {
  kind: "topics" | "sources";
  initialPage: CatalogPage<Item>;
  query?: string;
}) {
  const fetchPage = useCallback(
    async (cursor: string, signal: AbortSignal) => {
      const response = await readerRequest(
        `/api/v1${catalogHref(kind, { query, offset: cursor })}`,
        {
          signal,
          cache: "no-store",
        },
      );
      if (!response.ok) throw new AccountError(response.status);
      return (await response.json()) as { items: Item[]; next_cursor: string | null };
    },
    [kind, query],
  );
  const { pages, cursor, loading, error, loadMore } = useInfinitePages(initialPage, fetchPage);
  const items = uniquePageItems(flattenPageItems(pages), (item) => item.id);
  // This directory owns its filtered continuation; the boundary only loads or retries.
  return (
    <InfiniteScroll
      hasMore={cursor !== null}
      loading={loading}
      error={!!error}
      onLoadMore={loadMore}
      label={kind}
      nextHref={undefined}
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
              logoVariants={item.logo_variants}
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
              logoVariants={item.logo_variants}
              source
              description={item.description && <Markdown compact>{item.description}</Markdown>}
              followAction={<SourceFollow sourceId={item.id} returnTo={sourceHref(item)} compact />}
            />
          ),
        )}
      </div>
      {cursor !== null && (
        <Link className="button" rel="next" href={catalogHref(kind, { query, offset: cursor })}>
          Next page of {kind}
        </Link>
      )}
    </InfiniteScroll>
  );
}
