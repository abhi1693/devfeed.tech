"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import Link from "@/components/reader-link";
import { rememberReaderArticles } from "@/lib/reader-runtime";
import type { ReaderColumnFeed } from "@/lib/reader-preferences";
import { columnFeedLimits, type ColumnRequestQueue } from "@/lib/column-feed";
import { useColumnFeed } from "@/lib/use-column-feed";
import { parseFilters } from "@/lib/feed-query";
import { feedPlacements, readerFeeds } from "@/lib/reader-feed";
import { useArticleNavigation } from "./article-navigation";
import { ArticleStackedFrame, ArticleStackedRow } from "./article-stacked";
import { PartnerPlacements } from "./partner-placements";
import { useReadingAnchor } from "./use-reading-anchor";
import styles from "./columns-workspace.module.css";

export function ColumnFeed({
  columnId,
  name,
  feed,
  slug,
  queue,
  active,
  onActivate,
  onArticles,
}: {
  columnId: string;
  name: string;
  feed: ReaderColumnFeed;
  slug?: string;
  queue: ColumnRequestQueue;
  active: boolean;
  onActivate: () => void;
  onArticles: (id: string, articles: string[]) => void;
}) {
  const { pages, articles, cursor, loading, error, limited, loadMore, refresh } = useColumnFeed(
    feed,
    slug,
    queue,
  );
  const { setSequence } = useArticleNavigation();
  const sequence = useMemo(
    () => ({
      slugs: articles.map((article) => article.slug),
      hasMore: cursor !== null && !limited && !error,
      loading,
      loadMore,
    }),
    [articles, cursor, limited, error, loading, loadMore],
  );
  useEffect(() => {
    rememberReaderArticles(articles);
    onArticles(
      columnId,
      articles.map((article) => article.id),
    );
  }, [articles, columnId, onArticles]);
  useEffect(() => () => onArticles(columnId, []), [columnId, onArticles]);
  useEffect(() => {
    if (!active) return;
    setSequence(sequence);
    return () => setSequence((current) => (current === sequence ? null : current));
  }, [active, sequence, setSequence]);
  const page = pages[0];
  const { ref, beforeDisplay, afterDisplay } = useReadingAnchor(
    ".article-stacked-row:not(.partner-placement)",
  );
  const placements = useMemo(() => {
    const descriptor =
      feed.kind === "recommended"
        ? readerFeeds.home
        : feed.kind === "latest"
          ? readerFeeds.latest
          : readerFeeds.contextual;
    const context = feedPlacements(
      descriptor,
      parseFilters({}),
      feed.kind === "topic" ? { section: "topics", topicId: feed.id } : {},
    )!;
    return {
      ...context,
      ...(context.surface === "feed"
        ? { article_ids: page?.items.slice(0, columnFeedLimits.pageSize).map((item) => item.id) }
        : {}),
    };
  }, [feed, page]);
  const rows = (cards: ReactNode[] = []) => (
    <div ref={ref}>
      {!!articles.length && (
        <ArticleStackedFrame>
          {articles.flatMap((article, index) => [
            <ArticleStackedRow
              key={article.id}
              article={article}
              textOnly
              onOpen={() => {
                onActivate();
                setSequence(sequence);
              }}
            />,
            ...(index === 3 ? cards : []),
          ])}
        </ArticleStackedFrame>
      )}
    </div>
  );
  const preparing = page?.status === "refreshing";
  const changed = error?.status === 409;
  const recommended = feed.kind === "recommended";
  return (
    <div
      className={styles.columnScroll}
      role="region"
      aria-label={`Articles in ${name} column`}
      tabIndex={-1}
    >
      <PartnerPlacements
        {...placements}
        ad_formats={["feed"]}
        layout="columns"
        maxItems={(page?.items.length ?? 0) >= 4 ? 1 : 0}
        requestQueue={queue}
        beforeDisplay={beforeDisplay}
        afterDisplay={afterDisplay}
      >
        {rows}
      </PartnerPlacements>
      <div className={styles.feedStatus} aria-live="polite">
        {loading ? (
          <p role="status">Loading articles…</p>
        ) : error ? (
          <>
            <p role="status">
              {changed ? "Your feed has been updated." : "Couldn’t load articles in this column."}
            </p>
            <button className="button" onClick={changed ? refresh : () => void loadMore()}>
              {changed ? "Show updated feed" : "Try again"}
            </button>
          </>
        ) : (
          <>
            {preparing && (
              <>
                <p role="status">
                  {articles.length
                    ? page.feed_kind === "following"
                      ? "Recent articles from your follows while we prepare recommendations."
                      : "Your recommendations are being updated."
                    : "Your recommendations are being prepared."}
                </p>
                <button className="button" onClick={refresh}>
                  Check again
                </button>
              </>
            )}
            {!articles.length && !preparing && (
              <>
                <p role="status">
                  {recommended
                    ? page?.has_interests === false
                      ? "Follow sources or topics to get started."
                      : "No recommendations yet."
                    : "No articles yet."}
                </p>
                {recommended && page?.has_interests === false && (
                  <Link className="button" href="/settings/topics">
                    Choose topics
                  </Link>
                )}
              </>
            )}
            {limited ? (
              <p role="status">
                Showing up to {columnFeedLimits.articlesPerColumn} articles in this column.
              </p>
            ) : cursor !== null ? (
              <button className="button" onClick={() => void loadMore()}>
                Load more articles
              </button>
            ) : !!articles.length && !preparing ? (
              <p>You’re all caught up.</p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
