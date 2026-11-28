import { PublicPage } from "@/components/public-page";
import { CollectionUnavailable } from "@/components/collection-unavailable";
import { SourcesContent } from "@/components/sources-content";
import { catalogFilters } from "@/lib/catalog-page";
import type { SearchParams } from "@/lib/feed-query";
import { JsonLd } from "@/components/json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { catalogCanonical } from "@/lib/metadata";
import { catalogDirectoryMetadata, loadCatalogDirectory } from "@/lib/server/catalog-directory";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return catalogDirectoryMetadata("sources", await searchParams);
}
export default async function Sources({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const { offset, query, letter } = catalogFilters(params, "sources");
  const page = await loadCatalogDirectory("sources", offset, query, letter);
  if (!page) return <CollectionUnavailable kind="sources" />;
  return (
    <PublicPage
      kind="sources"
      content={Promise.resolve(
        <SourcesContent
          sources={page.items}
          offset={offset}
          query={query}
          initialPage={page}
          withShell={false}
        >
          <JsonLd
            data={collectionStructuredData(
              catalogCanonical("/sources", params),
              "Sources",
              page.items.map((item) => ({
                name: item.name,
                path: `/sources/${encodeURIComponent(item.slug)}`,
              })),
              { offset },
            )}
          />
        </SourcesContent>,
      )}
    />
  );
}
