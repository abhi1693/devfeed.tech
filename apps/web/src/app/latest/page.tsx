import { permanentRedirect } from "next/navigation";
import { FeedView } from "@/components/feed-view";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata, SITE_DESCRIPTION } from "@/lib/metadata";
import { hasUserSession } from "@/lib/api";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return feedMetadata("Developer news, tutorials & releases", SITE_DESCRIPTION, await searchParams);
}
export default async function Feed({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const filters = parseFilters(query);
  const explicitContentType = filters.content_type;
  const bareLatest = !filters.topic && !filters.source_id && !filters.tag;
  if (bareLatest && !explicitContentType && !(await hasUserSession()))
    filters.content_type = "article";
  if (filters.topic || filters.source_id || filters.tag || explicitContentType)
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({ filters });
}
