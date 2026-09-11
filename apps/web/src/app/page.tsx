import { permanentRedirect } from "next/navigation";
import { FeedView } from "@/components/feed-view";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  return feedMetadata(
    "Latest feed",
    "Developer news, tutorials, and articles.",
    await searchParams,
  );
}
export default async function Feed({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const filters = parseFilters(await searchParams);
  if (filters.topic || filters.source_id || filters.content_type)
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({ filters });
}
