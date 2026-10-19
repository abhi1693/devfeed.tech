import { cache } from "react";
import { CollectionUnavailable } from "@/components/collection-unavailable";
import { getTags, UserApiError } from "@/lib/api";
import { catalogOffset, catalogPage } from "@/lib/catalog-page";
import { pageMetadata, catalogCanonical } from "@/lib/metadata";
import { UserShell } from "@/components/user-shell";
import { TagsContent } from "@/components/tags-content";
import type { SearchParams } from "@/lib/feed-query";
export const dynamic = "force-dynamic";
function filters(params: SearchParams) {
  return {
    offset: catalogOffset(params.offset),
    query: typeof params.q === "string" ? params.q.trim().slice(0, 200) : "",
    letter:
      typeof params.letter === "string" && /^[A-Z#]$/.test(params.letter) ? params.letter : "",
  };
}
const load = cache(async (offset: number, query: string, letter: string) => {
  try {
    return await getTags(offset, 60, query, letter);
  } catch (error) {
    if (error instanceof UserApiError && error.status >= 500) return null;
    throw error;
  }
});
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const { offset, query, letter } = filters(params);
  if (!(await load(offset, query, letter)))
    return { title: "Page temporarily unavailable", robots: { index: false, follow: false } };
  return pageMetadata(
    "Explore tags",
    "Discover developer articles by tag.",
    catalogCanonical("/tags", params),
  );
}
export default async function Tags({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const { offset, query, letter } = filters(params);
  const tags = await load(offset, query, letter);
  if (!tags) return <CollectionUnavailable kind="tags" />;
  return (
    <UserShell section="tags">
      <TagsContent
        key={`${query}:${letter}:${offset}`}
        tags={tags}
        query={query}
        letter={letter}
        nextCursor={catalogPage(tags, offset).next_cursor}
      />
    </UserShell>
  );
}
