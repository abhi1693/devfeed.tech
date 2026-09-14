import { Suspense } from "react";
import type { Article } from "@/lib/types";
import { JsonLd } from "./json-ld";
import { articleStructuredData } from "@/lib/structured-data";
import { ArticleTopicBrief } from "./article-topic-brief";
import { ArticlePreviewContent } from "./article-preview-content";

export function ArticlePreview({ article }: { article: Article }) {
  const featuredTopic =
    article.topics.find((topic) => topic.role === "primary") ?? article.topics[0];
  return (
    <ArticlePreviewContent
      article={article}
      topicBrief={
        featuredTopic && (
          <Suspense fallback={<p role="status">Loading topic…</p>}>
            <ArticleTopicBrief topic={featuredTopic} articleSlug={article.slug} />
          </Suspense>
        )
      }
    >
      <JsonLd data={articleStructuredData(article)} />
    </ArticlePreviewContent>
  );
}
