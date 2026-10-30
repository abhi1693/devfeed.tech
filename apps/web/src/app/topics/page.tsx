import { PublicPage } from "@/components/public-page";
import { TopicsContent } from "@/components/topics-content";
import { getTopics } from "@/lib/api";
import { CATALOG_PAGE_SIZE, catalogOffset, catalogPage } from "@/lib/catalog-page";
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
  return <PublicPage kind="topics" content={Results({ query, offset })} />;
}
async function Results({ query, offset }: { query: SearchParams; offset: number }) {
  const page = catalogPage(await getTopics(offset, CATALOG_PAGE_SIZE + 1), offset);
  const topics = page.items;
  return (
    <TopicsContent topics={topics} offset={offset} initialPage={page} withShell={false}>
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
