import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { FeedView } from "@/components/feed-view";
import { contentTypeRoutes, feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
import { resolveReaderRoute } from "@/lib/reader-routes";

export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ contentType: string }>;
  searchParams: Promise<SearchParams>;
};
const resolve = cache((contentType: string) => {
  const route = resolveReaderRoute(`/${encodeURIComponent(contentType)}`);
  if (
    route?.type !== "reader" ||
    route.feed.mode !== "latest" ||
    !route.contentType ||
    route.detail
  )
    notFound();
  const name = contentTypeRoutes[route.contentType];
  return {
    type: route.contentType,
    name,
    title: name[0].toUpperCase() + name.slice(1),
    feedDescriptor: route.feed,
  };
});

export async function generateMetadata({ params, searchParams }: Props) {
  const { contentType } = await params;
  const { type, name, title } = resolve(contentType);
  return feedMetadata(title, `Developer ${name}.`, await searchParams, {
    content_type: type,
  });
}

export default async function ContentFeed({ params, searchParams }: Props) {
  const { contentType } = await params;
  const { type, title, feedDescriptor } = resolve(contentType);
  const query = await searchParams;
  const filters = parseFilters({ ...query, content_type: type });
  if (filters.topic || filters.source_id || filters.tag || "content_type" in query)
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({ filters, title, feedDescriptor });
}
