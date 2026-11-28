import { PublicPage } from "@/components/public-page";
import { CollectionUnavailable } from "@/components/collection-unavailable";
import { TopicsContent } from "@/components/topics-content";
import { catalogFilters } from "@/lib/catalog-page";
import type { SearchParams } from "@/lib/feed-query";
import { JsonLd } from "@/components/json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { catalogCanonical } from "@/lib/metadata";
import { catalogDirectoryMetadata, loadCatalogDirectory } from "@/lib/server/catalog-directory";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return catalogDirectoryMetadata("topics", await searchParams);
}
export default async function Topics({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const { offset, query, letter } = catalogFilters(params, "topics");
  const page = await loadCatalogDirectory("topics", offset, query, letter);
  if (!page) return <CollectionUnavailable kind="topics" />;
  return (
    <PublicPage
      kind="topics"
      content={Promise.resolve(
        <TopicsContent
          topics={page.items}
          offset={offset}
          query={query}
          initialPage={page}
          withShell={false}
        >
          <JsonLd
            data={collectionStructuredData(
              catalogCanonical("/topics", params),
              "Explore topics",
              page.items.map((item) => ({
                name: item.name,
                path: `/topics/${encodeURIComponent(item.slug)}`,
              })),
              { offset },
            )}
          />
        </TopicsContent>,
      )}
    />
  );
}
