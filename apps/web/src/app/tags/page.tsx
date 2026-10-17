import { getTags } from "@/lib/api";
import { catalogOffset, catalogPage } from "@/lib/catalog-page";
import { pageMetadata, catalogCanonical } from "@/lib/metadata";
import { UserShell } from "@/components/user-shell";
import { TagsContent } from "@/components/tags-content";
import type { SearchParams } from "@/lib/feed-query";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return pageMetadata(
    "Explore tags",
    "Discover developer articles by tag.",
    catalogCanonical("/tags", await searchParams),
  );
}
export default async function Tags({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const offset = catalogOffset(params.offset);
  const query = typeof params.q === "string" ? params.q.trim().slice(0, 200) : "";
  const letter =
    typeof params.letter === "string" && /^[A-Z#]$/.test(params.letter) ? params.letter : "";
  const tags = await getTags(offset, 60, query, letter);
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
