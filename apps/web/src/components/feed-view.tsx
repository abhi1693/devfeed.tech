import { JsonLd } from "./json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { canonicalUrl } from "@/lib/metadata";
import { getFeed, getFeedOptions } from "@/lib/api";
import { feedHref } from "@/lib/feed-query";
import { FeedContent, type FeedContentProps } from "./feed-content";

export async function FeedView({
  filters,
  title = "Latest feed",
  section = "feed",
  structuredData = true,
  ...props
}: Omit<FeedContentProps, "feed" | "options" | "children"> & {
  structuredData?: boolean;
}) {
  const [feed, options] = await Promise.allSettled([getFeed(filters), getFeedOptions(filters)]);
  return (
    <FeedContent
      {...props}
      filters={filters}
      title={title}
      section={section}
      feed={feed}
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
