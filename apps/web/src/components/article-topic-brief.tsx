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
      <div className="topic-brief-actions">
        <CatalogIcon url={details?.logo_url ?? null} />
        <TopicFollow topicId={topic.id} articleSlug={articleSlug} />
      </div>
      <h2>
        <Link href={`/topics/${encodeURIComponent(topic.slug)}`}>
          {topic.name}
        </Link>
      </h2>
      {(details?.ai_description || details?.description) && (
        <Markdown>{details.ai_description || details.description || ""}</Markdown>
      )}
      <Link
        className="text-link"
        href={`/topics/${encodeURIComponent(topic.slug)}`}
      >
        Explore topic
      </Link>
    </section>
  );
}
