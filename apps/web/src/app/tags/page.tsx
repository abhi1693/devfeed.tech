import { CollectionUnavailable } from "@/components/collection-unavailable";
import { catalogFilters } from "@/lib/catalog-page";
import { catalogDirectoryMetadata, loadCatalogDirectory } from "@/lib/server/catalog-directory";
import { UserShell } from "@/components/user-shell";
import { TagsContent } from "@/components/tags-content";
import type { SearchParams } from "@/lib/feed-query";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return catalogDirectoryMetadata("tags", await searchParams);
}
export default async function Tags({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const { offset, query, letter } = catalogFilters(await searchParams, "tags");
  const page = await loadCatalogDirectory("tags", offset, query, letter);
  if (!page) return <CollectionUnavailable kind="tags" />;
  return (
    <UserShell section="tags">
      <TagsContent
        key={`${query}:${letter}:${offset}`}
        tags={page.items}
        query={query}
        letter={letter}
        nextCursor={page.next_cursor}
        offset={offset}
      />
    </UserShell>
  );
}
