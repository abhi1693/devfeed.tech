import { Markdown } from "@devfeed/ui/markdown";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { getTopic } from "@/lib/api";
import { CatalogIcon } from "./catalog-icon";
import { TopicFollow } from "./topic-follow";

export async function ArticleTopicBrief({
  topic,
  articleSlug,
}: {
  topic: Article["topics"][number];
  articleSlug: string;
}) {
  // Load one featured topic, regardless of how many labels the article has.
  // Failure of optional context must not hide the article preview.
  const details = await getTopic(topic.slug).catch(() => null);
  return (
    <section className="topic-brief" aria-label={`About ${topic.name}`}>
      <p className="topic-brief-label">About this topic</p>
      <div className="topic-brief-heading">
        <CatalogIcon url={details?.logo_url ?? null} />
        <h2>
          <Link href={`/topics/${encodeURIComponent(topic.slug)}`}>
            {topic.name}
          </Link>
        </h2>
      </div>
      {(details?.ai_description || details?.description) && (
        <Markdown>{details.ai_description || details.description || ""}</Markdown>
      )}
      <div className="topic-brief-actions">
        <TopicFollow topicId={topic.id} articleSlug={articleSlug} />
      </div>
    </section>
  );
}
