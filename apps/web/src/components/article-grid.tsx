"use client";
import { useEffect, type ReactNode } from "react";
import { rememberReaderArticles } from "@/lib/reader-runtime";
import type { Article } from "@/lib/types";
import { safeExternalUrl } from "@/lib/feed-query";
import { EngagementProvider } from "./article-engagement";
import { ArticleCard } from "./article-card";
import { ArticleTableFrame, ArticleTableRow } from "./article-table";
import { useFeedPreferences } from "./feed-preferences";
import { DevCardPromo } from "./dev-card-promo";
import { LoadingSkeleton } from "./loading-skeleton";
import { PartnerPlacements, type PartnerPlacementContext } from "./partner-placements";
import { useReadingAnchor } from "./use-reading-anchor";
import type { PartnerPlacement } from "@/lib/partner-placements";
export type RecommendationReason = {
  kind: "followed_topic" | "liked_topic" | "related_topic" | "followed_source" | "followed_tag";
  topic_id?: string | null;
  seed_topic_id?: string | null;
};
export function ArticleGrid({
  articles,
  reasons,
  priority = true,
  showDevCard = false,
  waitForPreferences = false,
  placements,
  placementArticleCount,
}: {
  articles: Article[];
  reasons?: Record<string, RecommendationReason>;
  priority?: boolean;
  showDevCard?: boolean;
  waitForPreferences?: boolean;
  placements?: PartnerPlacementContext;
  /** Keep promotion slots within the initial page when more articles are appended. */
  placementArticleCount?: number;
}) {
  const { view, loading } = useFeedPreferences();
  const compact = view === "compact";
  const requestedFormats: PartnerPlacement["ad_format"][] = placements?.ad_formats ?? ["feed"];
  const placementFormats = requestedFormats.filter(
    (format) => format === "feed" || (!compact && format === "sponsored_content"),
  );
  const placementsReady = placements && !loading && placementFormats.length > 0;
  const {
    ref: gridRef,
    beforeDisplay,
    afterDisplay,
  } = useReadingAnchor(".article-card, .article-list-row:not(.partner-placement)");
  useEffect(() => rememberReaderArticles(articles), [articles]);
  const recommendations = Object.fromEntries(
    Object.entries(reasons ?? {}).map(([id, reason]) => [
      id,
      {
        followed_source: "From a source you follow",
        followed_tag: "From a tag you follow",
        followed_topic: "From a topic you follow",
        liked_topic: "Based on articles you like",
        related_topic: "Related to your interests",
      }[reason.kind],
    ]),
  );
  const count = Math.min(articles.length, placementArticleCount ?? articles.length);
  const slots = count >= 4 ? [4, ...(count >= 12 ? [12] : [])] : [];
  const grid = (cards: ReactNode[] = [], items: PartnerPlacement[] = [], expired = false) => {
    const promoted = new Set(
      expired || compact ? [] : items.flatMap((item) => (item.article ? [item.article.id] : [])),
    );
    const cells = cards.filter((_, index) =>
      compact
        ? items[index]?.ad_format === "feed"
        : items[index]?.ad_format === "feed" || items[index]?.ad_format === "sponsored_content",
    );
    const visibleArticles = articles.filter((article) => !promoted.has(article.id));
    const firstImage = visibleArticles.findIndex((article) => safeExternalUrl(article.image_url));
    const rows = visibleArticles.flatMap((article, index) => {
      const slot = slots.indexOf(index + 1);
      return [
        compact ? (
          <ArticleTableRow key={article.id} article={article} />
        ) : (
          <ArticleCard
            key={article.id}
            article={article}
            recommendation={recommendations[article.id]}
            priority={priority && index === firstImage}
          />
        ),
        ...(slot >= 0 && cells[slot] ? [cells[slot]] : []),
      ];
    });
    return (
      <div ref={gridRef} className="article-placement-feed">
        {compact ? (
          <ArticleTableFrame showHeader={priority}>{rows}</ArticleTableFrame>
        ) : (
          <div className="article-grid">{rows}</div>
        )}
      </div>
    );
  };
  if (waitForPreferences && loading) return <LoadingSkeleton />;
  return (
    <EngagementProvider articleIds={articles.map((article) => article.id)}>
      {placements ? (
        <PartnerPlacements
          {...placements}
          ad_formats={placementFormats}
          layout={compact ? "table" : undefined}
          maxItems={placementsReady ? slots.length : 0}
          beforeDisplay={beforeDisplay}
          afterDisplay={afterDisplay}
        >
          {grid}
        </PartnerPlacements>
      ) : (
        grid()
      )}
      {showDevCard && <DevCardPromo />}
    </EngagementProvider>
  );
}
