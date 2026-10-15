import { ReaderDisclosure } from "./reader-disclosure";
import { SourceFollow } from "./source-follow";
import { TopicFollow } from "./topic-follow";
import { CatalogIcon } from "./catalog-icon";
import { Markdown } from "@devfeed/ui/markdown";
import Link from "@/components/reader-link";
import { ArrowRight, ArrowUpRight, Rss, SearchX } from "lucide-react";
import { feedHref, feedParams, sourceHref, type FeedFilters } from "@/lib/feed-query";
import { UserShell } from "@/components/user-shell";
import { InfiniteFeed } from "@/components/infinite-feed";
import { FeedFiltersBar } from "@/components/feed-filters";
import { RetryFeed } from "@/components/retry-feed";
import { feedPlacements, type FeedDescriptor } from "@/lib/reader-feed";
import { CollectionFeedPlacements } from "./collection-feed-placements";
import type { PartnerPlacementContext } from "./partner-placements";

import type { ReactNode } from "react";
import type { FeedPage, FeedOptions } from "@/lib/types";

export type FeedContentProps = {
  filters: FeedFilters;
  title?: string;
  description?: string | null;
  logoUrl?: string | null;
  logoVariants?: { url: string; width: number }[];
  topicId?: string;
  section?: "feed" | "topics" | "sources";
  feed: PromiseSettledResult<FeedPage>;
  options: PromiseSettledResult<FeedOptions>;
  children?: ReactNode;
  /** PublicPage owns the persistent shell for streamed listings. */
  withShell?: boolean;
  feedDescriptor: FeedDescriptor;
};

export function FeedContent({
  filters,
  title = "Latest feed",
  description,
  logoUrl,
  logoVariants,
  topicId,
  section = "feed",
  feed,
  options,
  children,
  withShell = true,
  feedDescriptor,
}: FeedContentProps) {
  const filtered = Object.entries(filters).some(
    ([key, value]) => key !== "cursor" && key !== "source_slug" && value,
  );
  const initialArticleIds =
    feed.status === "fulfilled"
      ? [...new Set(feed.value.items.map((article) => article.id))].slice(0, 24)
      : [];
  let sidebar: PartnerPlacementContext | undefined;
  if (feedDescriptor.capabilities.placements && !filters.cursor && initialArticleIds.length) {
    if (section === "topics" && topicId)
      sidebar = { surface: "topic", topic_id: topicId, ad_formats: ["engagement"] };
    else if (section === "sources" && filters.source_id && initialArticleIds.length >= 4)
      sidebar = {
        surface: "feed",
        article_ids: initialArticleIds,
        ad_formats: ["engagement"],
        allow_sponsored_fallback: false,
      };
  }
  const content = (
    <>
      {children}
      <section className="feed-header" aria-label="Feed controls">
        <div
          className={
            filters.q || section !== "feed"
              ? `page-heading feed-heading${section === "sources" ? " source-feed-heading" : section === "topics" ? " topic-feed-heading" : ""}`
              : "sr-only"
          }
        >
          <div>
            <div className="feed-title">
              {(logoUrl || section === "sources") && (
                <CatalogIcon
                  url={logoUrl ?? null}
                  variants={logoVariants}
                  source={section === "sources"}
                />
              )}
              <h1>{filters.q ? `Results for “${filters.q}”` : title}</h1>
            </div>
            {description &&
              (section === "sources" ? (
                <div className="source-description">
                  <Markdown>{description}</Markdown>
                </div>
              ) : (
                <ReaderDisclosure className="topic-description" title={<>About {title}</>}>
                  {section === "topics" ? <p>{description}</p> : <Markdown>{description}</Markdown>}
                </ReaderDisclosure>
              ))}
          </div>
          {section === "sources" && filters.source_id && (
            <SourceFollow
              sourceId={filters.source_id}
              returnTo={feedHref(filters, { cursor: filters.cursor })}
            />
          )}
          {section === "topics" && topicId && (
            <TopicFollow
              key={topicId}
              topicId={topicId}
              returnTo={feedHref(filters, { cursor: filters.cursor })}
            />
          )}
        </div>
        <FeedFiltersBar
          key={feedParams(filters).toString()}
          filters={filters}
          topicPage={section === "topics"}
          sourcePage={section === "sources"}
          sources={options.status === "fulfilled" ? options.value.sources : []}
          availableTypes={options.status === "fulfilled" ? options.value.content_types : []}
        />
      </section>
      {feed.status === "rejected" ? (
        <section className="empty-state" role="status">
          <Rss size={35} />
          <h2>{filters.cursor ? "This page could not be loaded" : "Couldn’t load the feed"}</h2>
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
          <CollectionFeedPlacements key={feedParams(filters).toString()} context={sidebar}>
            <InfiniteFeed
              initialPage={feed.value}
              filters={filters}
              placements={feedPlacements(feedDescriptor, filters, { section, topicId })}
            />
          </CollectionFeedPlacements>
          {filters.cursor && (
            <div className="pagination">
              <Link className="button" href={feedHref(filters)}>
                Back to latest
              </Link>
            </div>
          )}
        </>
      ) : (
        <section className="empty-state">
          <div className="empty-icon">{filtered ? <SearchX size={32} /> : <Rss size={32} />}</div>
          <h2>{filtered ? "No articles match these filters" : "No articles yet"}</h2>
          <p>
            {filtered
              ? "Broaden your search or explore another topic."
              : "Published articles will appear here."}
          </p>
          <Link
            className="button primary"
            href={
              filtered
                ? section === "sources"
                  ? sourceHref({ id: filters.source_id, slug: filters.source_slug })
                  : "/"
                : "/topics"
            }
          >
            {filtered ? "Clear filters" : "Explore topics"}
            <ArrowUpRight size={17} />
          </Link>
        </section>
      )}
    </>
  );
  return withShell ? (
    <UserShell filters={filters} section={section}>
      {content}
    </UserShell>
  ) : (
    content
  );
}
