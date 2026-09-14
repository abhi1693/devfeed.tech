import { JsonLd } from "./json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { canonicalUrl } from "@/lib/metadata";
import { getFeed, getFeedOptions, getTopics } from "@/lib/api";
import { feedHref } from "@/lib/feed-query";
import { FeedContent, type FeedContentProps } from "./feed-content";

export async function FeedView({
  filters,
  title = "Latest feed",
  section = "feed",
  structuredData = true,
  ...props
}: Omit<FeedContentProps, "feed" | "topics" | "options" | "children"> & {
  structuredData?: boolean;
}) {
  const [feed, topics, options] = await Promise.allSettled([
    getFeed(filters),
    getTopics(0, 12),
    getFeedOptions(filters),
  ]);
  return (
    <FeedContent
      {...props}
      filters={filters}
      title={title}
      section={section}
      feed={feed}
      topics={topics}
      options={options}
    >
      {structuredData && feed.status === "fulfilled" && (
        <JsonLd
          data={collectionStructuredData(
            canonicalUrl(feedHref(filters, { cursor: filters.cursor })),
            filters.q ? `Results for “${filters.q}”` : title,
            feed.value.items.map((article) => ({
              name: article.title,
              path: `/articles/${encodeURIComponent(article.slug)}`,
            })),
            {
              parent:
                section === "topics"
                  ? { name: "Topics", path: "/topics" }
                  : section === "sources"
                    ? { name: "Sources", path: "/sources" }
                    : undefined,
            },
          )}
        />
      )}
    </FeedContent>
  );
}
