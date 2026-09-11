"use client";
import type { Article } from "@/lib/types";
import { EngagementProvider } from "./article-engagement";
import { ArticleCard } from "./article-card";
export type RecommendationReason = {
  kind: "followed_topic" | "liked_topic" | "related_topic";
  topic_id: string;
  seed_topic_id: string;
};
export function ArticleGrid({
  articles,
  reasons,
  priority = true,
}: {
  articles: Article[];
  reasons?: Record<string, RecommendationReason>;
  priority?: boolean;
}) {
  return (
    <EngagementProvider articleIds={articles.map((article) => article.id)}>
      <div className="article-grid">
        {articles.map((article, index) => (
          <ArticleCard
            key={article.id}
            article={article}
            recommendation={
              reasons?.[article.id]
                ? {
                    followed_topic: "From a topic you follow",
                    liked_topic: "Based on articles you like",
                    related_topic: "Related to your interests",
                  }[reasons[article.id].kind]
                : undefined
            }
            priority={priority && index < 4}
          />
        ))}
      </div>
    </EngagementProvider>
  );
}
