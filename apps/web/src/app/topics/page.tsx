import { TopicsContent } from "@/components/topics-content";
import { getTopics } from "@/lib/api";
import { catalogOffset } from "@/lib/catalog-page";
import type { SearchParams } from "@/lib/feed-query";
import { JsonLd } from "@/components/json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { catalogCanonical, pageMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return pageMetadata(
    "Explore topics",
    "Explore developer topics and find the latest published articles.",
    catalogCanonical("/topics", await searchParams),
  );
}
export default async function Topics({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const offset = catalogOffset(query.offset);
  const topics = await getTopics(offset);
  return (
    <TopicsContent topics={topics} offset={offset}>
      <JsonLd
        data={collectionStructuredData(
          catalogCanonical("/topics", query),
          "Explore topics",
          topics.map((item) => ({
            name: item.name,
            path: `/topics/${encodeURIComponent(item.slug)}`,
          })),
          { offset },
        )}
      />
    </TopicsContent>
  );
}
