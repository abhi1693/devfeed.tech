import { SourcesContent } from "@/components/sources-content";
import { getSources } from "@/lib/api";
import { catalogOffset } from "@/lib/catalog-page";
import type { SearchParams } from "@/lib/feed-query";
import { JsonLd } from "@/components/json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { catalogCanonical, pageMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return pageMetadata(
    "Sources",
    "Discover sources publishing developer news, tutorials, and releases.",
    catalogCanonical("/sources", await searchParams),
  );
}
export default async function Sources({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const offset = catalogOffset(query.offset);
  const sources = await getSources(offset, 60);
  return (
    <SourcesContent sources={sources} offset={offset}>
      <JsonLd
        data={collectionStructuredData(
          catalogCanonical("/sources", query),
          "Sources",
          sources.map((item) => ({
            name: item.name,
            path: `/sources/${encodeURIComponent(item.slug)}`,
          })),
          { offset },
        )}
      />
    </SourcesContent>
  );
}
