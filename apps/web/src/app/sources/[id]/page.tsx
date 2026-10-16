import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { getSource, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { collectionRoute } from "@/lib/reader-routes";
import { readerFeeds } from "@/lib/reader-feed";
import { feedMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ id: string; contentType?: string }>;
  searchParams: Promise<SearchParams>;
};
const load = cache(async (id: string, contentType?: string) => {
  const route = collectionRoute("sources", id, contentType);
  if (!route) notFound();
  try {
    return { route, item: await getSource(route.slug) };
  } catch (error) {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  }
});
function redirectAlias(
  id: string,
  slug: string,
  contentType: string | undefined,
  query: SearchParams,
) {
  if (id !== slug) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query))
      for (const entry of Array.isArray(value) ? value : value === undefined ? [] : [value])
        params.append(key, entry);
    permanentRedirect(
      `/sources/${encodeURIComponent(slug)}${contentType ? `/${contentType}` : ""}${params.size ? `?${params}` : ""}`,
    );
  }
}
export async function generateMetadata({ params, searchParams }: Props) {
  const { id, contentType } = await params;
  const { route, item } = await load(id, contentType);
  const query = await searchParams;
  redirectAlias(id, item.slug, contentType, query);
  return feedMetadata(item.name, item.description || `Articles from ${item.name}.`, query, {
    source_id: item.id,
    source_slug: item.slug,
    ...(route.contentType ? { content_type: route.contentType } : {}),
  });
}
export default async function Page({ params, searchParams }: Props) {
  const { id, contentType } = await params;
  const { route, item } = await load(id, contentType);
  const query = await searchParams;
  const filters = {
    ...parseFilters({
      ...query,
      ...(route.contentType ? { content_type: route.contentType } : {}),
      source_id: item.id,
    }),
    source_slug: item.slug,
  };
  redirectAlias(id, item.slug, contentType, query);
  if (
    (!route.contentType && filters.content_type) ||
    (route.contentType && "content_type" in query)
  )
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({
    filters,
    feedDescriptor: readerFeeds.contextual,
    title: item.name,
    description: item.description,
    logoUrl: item.logo_url,
    logoVariants: item.logo_variants,
    section: "sources",
  });
}
