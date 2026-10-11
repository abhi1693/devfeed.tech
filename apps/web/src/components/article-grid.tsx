"use client";
import { useEffect, type ReactNode } from "react";
import { rememberReaderArticles } from "@/lib/reader-runtime";
import type { Article } from "@/lib/types";
import { EngagementProvider } from "./article-engagement";
import { ArticleCard } from "./article-card";
import { ArticleTable } from "./article-table";
import { useFeedPreferences } from "./feed-preferences";
import { DevCardPromo } from "./dev-card-promo";
import { LoadingSkeleton } from "./loading-skeleton";
import { PartnerPlacements, type PartnerPlacementContext } from "./partner-placements";
import { useReadingAnchor } from "./use-reading-anchor";
export type RecommendationReason = {
  kind: "followed_topic" | "liked_topic" | "related_topic" | "followed_source";
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
  const { ref: gridRef, beforeDisplay, afterDisplay } = useReadingAnchor(".article-card");
  useEffect(() => rememberReaderArticles(articles), [articles]);
  const recommendations = Object.fromEntries(
    Object.entries(reasons ?? {}).map(([id, reason]) => [
      id,
      {
        followed_source: "From a source you follow",
        followed_topic: "From a topic you follow",
        liked_topic: "Based on articles you like",
        related_topic: "Related to your interests",
      }[reason.kind],
    ]),
  );
  const count = Math.min(articles.length, placementArticleCount ?? articles.length);
  const slots = count ? [Math.min(4, count), ...(count >= 12 ? [12] : [])] : [];
  const grid = (cards: ReactNode[] = []) => (
    <div className="article-grid" ref={gridRef}>
      {articles.flatMap((article, index) => {
        const slot = slots.indexOf(index + 1);
        return [
          <ArticleCard
            key={article.id}
            article={article}
            recommendation={recommendations[article.id]}
            priority={priority && index === 0}
          />,
          ...(slot >= 0 && cards[slot] ? [cards[slot]] : []),
        ];
      })}
    </div>
  );
  if (waitForPreferences && loading) return <LoadingSkeleton />;
  return (
    <EngagementProvider articleIds={articles.map((article) => article.id)}>
      {view === "compact" ? (
        <ArticleTable articles={articles} showHeader={priority} />
      ) : placements && slots.length ? (
        <PartnerPlacements
          {...placements}
          maxItems={slots.length}
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
