import Link from "next/link";
import { ArrowRight, ArrowUpRight, Clock3, Rss, SearchX } from "lucide-react";
import { getFeed, getSources, getTopics } from "@/lib/api";
import { feedHref, type FeedFilters } from "@/lib/feed-query";
import { UserShell } from "@/components/user-shell";
import { ArticleGrid } from "@/components/article-grid";
import { FeedFiltersBar } from "@/components/feed-filters";
import { RetryFeed } from "@/components/retry-feed";

export async function FeedView({
  filters,
  title = "Latest feed",
  description,
  section = "feed",
}: {
  filters: FeedFilters;
  title?: string;
  description?: string | null;
  section?: "feed" | "topics" | "sources";
}) {
  const [feed, topics, sources] = await Promise.allSettled([
    getFeed(filters),
    getTopics(0, 12),
    getSources(),
  ]);
  const filtered = Object.entries(filters).some(
    ([key, value]) => key !== "cursor" && value,
  );
  return (
    <UserShell filters={filters} section={section}>
      <section className="feed-header" aria-label="Feed controls">
        <div className="page-heading feed-heading">
          <div>
            <h1>{filters.q ? `Results for “${filters.q}”` : title}</h1>
            {description && <p>{description}</p>}
          </div>
          <span className="sort-label">
            <Clock3 size={14} aria-hidden="true" />
            Newest first
          </span>
        </div>
        <FeedFiltersBar
          filters={filters}
          sources={sources.status === "fulfilled" ? sources.value : []}
        />
      </section>
      {feed.status === "rejected" ? (
        <section className="empty-state" role="status">
          <Rss size={35} />
          <h2>
            {filters.cursor
              ? "This page could not be loaded"
              : "Couldn’t load the feed"}
          </h2>
          <p>
            {filters.cursor
              ? "Start from the latest articles and try again."
              : "We couldn’t reach the feed. Please try again shortly."}
          </p>
          {filters.cursor ? (
            <Link className="button primary" href={feedHref(filters)}>
              Back to latest
              <ArrowRight size={16} />
            </Link>
          ) : (
            <RetryFeed />
          )}
        </section>
      ) : feed.value.items.length ? (
        <>
          <ArticleGrid articles={feed.value.items} />
          <div className="pagination">
            {filters.cursor && (
              <Link className="button" href={feedHref(filters)}>
                Back to latest
              </Link>
            )}
            {feed.value.next_cursor ? (
              <Link
                className="button primary"
                href={feedHref(filters, { cursor: feed.value.next_cursor })}
              >
                More articles
                <ArrowRight size={16} />
              </Link>
            ) : (
              <p>No more articles.</p>
            )}
          </div>
        </>
      ) : (
        <section className="empty-state">
          <div className="empty-icon">
            {filtered ? <SearchX size={32} /> : <Rss size={32} />}
          </div>
          <h2>
            {filtered ? "No articles match these filters" : "No articles yet"}
          </h2>
          <p>
            {filtered
              ? "Broaden your search or explore another topic."
              : "Published articles will appear here."}
          </p>
          <Link className="button primary" href={filtered ? "/" : "/topics"}>
            {filtered ? "Clear filters" : "Explore topics"}
            <ArrowUpRight size={17} />
          </Link>
        </section>
      )}
      {topics.status === "fulfilled" && topics.value.length > 0 && (
        <section className="discovery-strip">
          <h2>Explore topics</h2>
          <div className="topic-pills">
            {topics.value.slice(0, 6).map((item) => (
              <Link
                key={item.id}
                href={`/topics/${encodeURIComponent(item.slug)}`}
              >
                #{item.name}
                <ArrowUpRight size={13} />
              </Link>
            ))}
          </div>
        </section>
      )}
    </UserShell>
  );
}
