import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { getTopic, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { collectionRoute } from "@/lib/reader-routes";
import { collectionFeedMetadata } from "@/lib/server/collection-feed-metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ slug: string; contentType?: string }>;
  searchParams: Promise<SearchParams>;
};
const load = cache(async (slug: string, contentType?: string) => {
  const route = collectionRoute("topics", slug, contentType);
  if (!route) notFound();
  try {
    return { route, item: await getTopic(route.slug) };
  } catch (error) {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  }
});
export async function generateMetadata({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const { route, item } = await load(slug, contentType);
  return collectionFeedMetadata(
    item.name,
    item.description || `Articles about ${item.name}.`,
    await searchParams,
    { topic: item.slug, ...(route.contentType ? { content_type: route.contentType } : {}) },
  );
}
export default async function Page({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const { route, item } = await load(slug, contentType);
  const query = await searchParams;
  const filters = parseFilters({
    ...query,
    ...(route.contentType ? { content_type: route.contentType } : {}),
    topic: route.slug,
  });
  if (
    (!route.contentType && filters.content_type) ||
    (route.contentType && "content_type" in query)
  )
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({
    filters,
    title: item.name,
    topicId: item.id,
    logoUrl: item.logo_url,
    logoVariants: item.logo_variants,
    description: item.description,
    section: "topics",
  });
}
