import Link from "next/link";
import type { Article, Topic } from "@/lib/types";
import { CatalogIcon } from "./catalog-icon";
import { TopicFollow } from "./topic-follow";

export function ArticleTopicBriefContent({
  topic,
  articleSlug,
  details,
}: {
  topic: Article["topics"][number];
  articleSlug: string;
  details?: Topic | null;
}) {
  return (
    <section className="topic-brief" aria-label={`About ${topic.name}`}>
      <p className="topic-brief-label">About this topic</p>
      <div className="topic-brief-heading">
        <CatalogIcon url={details?.logo_url ?? null} />
        <h2>
          <Link href={`/topics/${encodeURIComponent(topic.slug)}`}>{topic.name}</Link>
        </h2>
      </div>
      {(details?.ai_description || details?.description) && (
        <p className="topic-brief-description">{details.ai_description || details.description}</p>
      )}
      <div className="topic-brief-actions">
        <TopicFollow topicId={topic.id} articleSlug={articleSlug} />
      </div>
    </section>
  );
}
