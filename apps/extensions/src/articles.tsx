import { ArticleUnavailable } from "../../web/src/components/article-unavailable";
import { RetryButton } from "@devfeed/ui/retry-button";
import { useEffect, useState } from "react";
import type { Article, Topic } from "../../web/src/lib/types";
import { ArticleModal } from "../../web/src/components/article-modal";
import { ArticlePreviewContent } from "../../web/src/components/article-preview-content";
import { ArticleTopicBriefContent } from "../../web/src/components/article-topic-brief-content";
import { LoadingSkeleton } from "../../web/src/components/loading-skeleton";
import { readerRequest } from "../../web/src/lib/reader-runtime";
import { publicOrigin } from "./transport";

import { cachedArticle, cachedTopic, rememberArticles, rememberTopics } from "./public-cache";

async function topicDetails(article: Article, signal: AbortSignal) {
  const featured = article.topics.find((topic) => topic.role === "primary") ?? article.topics[0];
  if (!featured) return null;
  const known = cachedTopic(featured.slug);
  if (known) return known;
  const response = await readerRequest(`/api/v1/topics/${encodeURIComponent(featured.slug)}`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Topic unavailable");
  const topic = (await response.json()) as Topic;
  rememberTopics([topic]);
  return topic;
}

export function Preview({ slug, direct }: { slug: string; direct: boolean }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    slug: string;
    article?: Article;
    topic?: Topic | null;
    failed?: boolean;
  }>();
  useEffect(() => {
    const controller = new AbortController();
    const cached = cachedArticle(slug);
    if (cached) {
      const featured = cached.topics.find((topic) => topic.role === "primary") ?? cached.topics[0];
      setState({ slug, article: cached, topic: featured ? cachedTopic(featured.slug) : null });
    }
    void readerRequest(`/api/v1/articles/${encodeURIComponent(slug)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Article unavailable");
        const value = (await response.json()) as { article: Article; topic: Topic | null };
        if (!controller.signal.aborted) {
          rememberArticles([value.article]);
          if (value.topic) rememberTopics([value.topic]);
          setState({ slug, ...value });
        }
      })
      .catch(async () => {
        if (controller.signal.aborted) return;
        if (!cached) {
          setState({ slug, failed: true });
          return;
        }
        const topic = await topicDetails(cached, controller.signal).catch(() => null);
        if (!controller.signal.aborted) setState({ slug, article: cached, topic });
      });
    return () => controller.abort();
  }, [slug, attempt]);
  const current = state?.slug === slug ? state : undefined;
  const featured =
    current?.article?.topics.find((topic) => topic.role === "primary") ??
    current?.article?.topics[0];
  return (
    <ArticleModal
      direct={direct}
      slug={slug}
      canonical={`${publicOrigin}/articles/${encodeURIComponent(slug)}`}
    >
      {current?.article ? (
        <ArticlePreviewContent
          article={current.article}
          topicBrief={
            featured && (
              <ArticleTopicBriefContent
                topic={featured}
                articleSlug={current.article.slug}
                details={current.topic}
              />
            )
          }
        />
      ) : current?.failed ? (
        <ArticleUnavailable>
          <RetryButton
            onRetry={() => {
              setState(undefined);
              setAttempt((value) => value + 1);
            }}
          />
        </ArticleUnavailable>
      ) : (
        <LoadingSkeleton kind="form" label="Loading article…" />
      )}
    </ArticleModal>
  );
}
