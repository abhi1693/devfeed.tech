import { notFound, permanentRedirect } from "next/navigation";
import { FeedView } from "@/components/feed-view";
import { contentTypeFromRoute, feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";

export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ contentType: string }>;
  searchParams: Promise<SearchParams>;
};

export async function generateMetadata({ params, searchParams }: Props) {
  const { contentType } = await params;
  if (!contentTypeFromRoute(contentType)) notFound();
  const title = contentType[0].toUpperCase() + contentType.slice(1);
  return feedMetadata(title, `Developer ${contentType}.`, await searchParams);
}

export default async function ContentFeed({ params, searchParams }: Props) {
  const { contentType } = await params;
  const type = contentTypeFromRoute(contentType);
  if (!type) notFound();
  const query = await searchParams;
  const filters = parseFilters({ ...query, content_type: type });
  if (filters.topic || filters.source_id || "content_type" in query)
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({ filters, title: contentType[0].toUpperCase() + contentType.slice(1) });
}
