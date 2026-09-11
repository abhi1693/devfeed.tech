import { SourceFollow } from "./source-follow";
import { CatalogIcon } from "./catalog-icon";
import { Markdown } from "@devfeed/ui/markdown";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, Rss, SearchX } from "lucide-react";
import { getFeed, getFeedOptions, getTopics } from "@/lib/api";
import { feedHref, feedParams, type FeedFilters } from "@/lib/feed-query";
import { UserShell } from "@/components/user-shell";
import { InfiniteFeed } from "@/components/infinite-feed";
import { FeedFiltersBar } from "@/components/feed-filters";
import { RetryFeed } from "@/components/retry-feed";

export async function FeedView({
  filters,
  title = "Latest feed",
  description,
  logoUrl,
  section = "feed",
}: {
  filters: FeedFilters;
  title?: string;
  description?: string | null;
  logoUrl?: string | null;
  section?: "feed" | "topics" | "sources";
}) {
  const [feed, topics, options] = await Promise.allSettled([
    getFeed(filters),
    getTopics(0, 12),
    getFeedOptions(filters),
  ]);
  const filtered = Object.entries(filters).some(
    ([key, value]) => key !== "cursor" && value,
  );
  return (
    <UserShell filters={filters} section={section}>
      <section className="feed-header" aria-label="Feed controls">
        <div className={filters.q || section !== "feed" ? "page-heading feed-heading" : "sr-only"}>
          <div>
            <div className="feed-title">
              {logoUrl && <CatalogIcon url={logoUrl} />}
              <h1>{filters.q ? `Results for “${filters.q}”` : title}</h1>
            </div>
            {description && <details className="topic-description"><summary>About {title}</summary><Markdown>{description}</Markdown></details>}
          </div>
        </div>
        {section === "sources" && filters.source_id && <SourceFollow sourceId={filters.source_id} returnTo={`/sources/${filters.source_id}`} />}
        <FeedFiltersBar
          key={feedParams(filters).toString()}
          filters={filters}
          sources={options.status === "fulfilled" ? options.value.sources : []}
          availableTypes={options.status === "fulfilled" ? options.value.content_types : []}
          availableLanguages={options.status === "fulfilled" ? options.value.languages : []}
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
          <InfiniteFeed key={feedParams(filters).toString()} initialPage={feed.value} filters={filters} />
          {filters.cursor && (
            <div className="pagination">
              <Link className="button" href={feedHref(filters)}>Back to latest</Link>
            </div>
          )}
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
