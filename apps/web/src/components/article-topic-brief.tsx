import type { Article } from "@/lib/types";
import { getTopic } from "@/lib/api";
import { ArticleTopicBriefContent } from "./article-topic-brief-content";

export async function ArticleTopicBrief({
  topic,
  articleSlug,
}: {
  topic: Article["topics"][number];
  articleSlug: string;
}) {
  const details = await getTopic(topic.slug).catch(() => null);
  return <ArticleTopicBriefContent topic={topic} articleSlug={articleSlug} details={details} />;
}
