"use client";
import type { Article } from "@/lib/types";
import { EngagementProvider } from "./article-engagement";
import { ArticleCard } from "./article-card";
export function ArticleGrid({ articles }: { articles: Article[] }) {
  return (
    <EngagementProvider articleIds={articles.map((article) => article.id)}>
      <div className="article-grid">
        {articles.map((article, index) => (
          <ArticleCard
            key={article.id}
            article={article}
            priority={index < 4}
          />
        ))}
      </div>
    </EngagementProvider>
  );
}
