"use client";
import { useEffect } from "react";
import { rememberReaderArticles } from "@/lib/reader-runtime";
import type { Article } from "@/lib/types";
import { EngagementProvider } from "./article-engagement";
import { ArticleCard } from "./article-card";
import { ArticleTable } from "./article-table";
import { useFeedPreferences } from "./feed-preferences";
import { DevCardPromo } from "./dev-card-promo";
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
}: {
  articles: Article[];
  reasons?: Record<string, RecommendationReason>;
  priority?: boolean;
  showDevCard?: boolean;
}) {
  const { view } = useFeedPreferences();
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
  return (
    <EngagementProvider articleIds={articles.map((article) => article.id)}>
      {view === "compact" ? (
        <ArticleTable articles={articles} recommendations={recommendations} showHeader={priority} />
      ) : (
        <div className="article-grid">
          {articles.map((article, index) => (
            <ArticleCard
              key={article.id}
              article={article}
              recommendation={recommendations[article.id]}
              priority={priority && index < 4}
            />
          ))}
        </div>
      )}
      {showDevCard && <DevCardPromo />}
    </EngagementProvider>
  );
}
