import { PublicPage } from "@/components/public-page";
import { permanentRedirect } from "next/navigation";
import { FeedView } from "@/components/feed-view";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata, SITE_DESCRIPTION } from "@/lib/metadata";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return feedMetadata("Developer news, tutorials & releases", SITE_DESCRIPTION, await searchParams);
}
export default async function Feed({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const filters = parseFilters(query);
  if (filters.topic || filters.source_id || filters.tag || filters.content_type)
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return <PublicPage filters={filters} content={FeedView({ filters, withShell: false })} />;
}
