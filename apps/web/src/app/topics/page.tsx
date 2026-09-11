import { Markdown } from "@devfeed/ui/markdown";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Hash } from "lucide-react";
import { getTopics } from "@/lib/api";
import { CatalogIcon } from "@/components/catalog-icon";
import { UserShell } from "@/components/user-shell";
import type { SearchParams } from "@/lib/feed-query";
export const dynamic = "force-dynamic";
export const metadata = { title: "Explore topics" };
export default async function Topics({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const query = await searchParams;
  const offset =
    typeof query.offset === "string" && /^\d+$/.test(query.offset)
      ? Math.min(Number(query.offset), 1000000)
      : 0;
  const topics = await getTopics(offset);
  return (
    <UserShell section="topics">
      <div className="page-heading">
        <div>
          <h1>Explore topics</h1>
        </div>
      </div>
      {topics.length ? (
        <div className="topic-grid">
          {topics.map((topic) => (
            <Link
              key={topic.id}
              href={`/topics/${encodeURIComponent(topic.slug)}`}
              className="topic-card catalog-card"
            >
              <div className="topic-card-heading">
                <CatalogIcon url={topic.logo_url} />
                <h2>{topic.name}</h2>
              </div>
              {(topic.description || topic.ai_description) && (
                <Markdown compact>{topic.description || topic.ai_description || ""}</Markdown>
              )}
            </Link>
          ))}
        </div>
      ) : (
        <section className="empty-state">
          <Hash size={32} />
          <h2>{offset ? "No topics on this page" : "No topics with published articles yet"}</h2>
          <Link className="button" href="/">
            Back to the feed
          </Link>
        </section>
      )}
      <nav className="pagination" aria-label="Topic pages">
        {offset > 0 && (
          <Link
            className="button"
            href={`/topics?offset=${Math.max(0, offset - 60)}`}
          >
            <ArrowLeft size={16} />
            Previous topics
          </Link>
        )}
        {topics.length === 60 && (
          <Link className="button" href={`/topics?offset=${offset + 60}`}>
            More topics
            <ArrowRight size={16} />
          </Link>
        )}
      </nav>
    </UserShell>
  );
}
