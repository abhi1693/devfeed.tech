import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { getSource, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { contentTypeFromRoute, feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ id: string; contentType?: string }>;
  searchParams: Promise<SearchParams>;
};
const load = cache(async (id: string) => {
  if (!/^[a-z0-9][a-z0-9-]{0,199}$/i.test(id)) notFound();
  try {
    return await getSource(id);
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
  const type = contentType ? contentTypeFromRoute(contentType) : undefined;
  if (contentType && !type) notFound();
  const item = await load(id);
  const query = await searchParams;
  redirectAlias(id, item.slug, contentType, query);
  return feedMetadata(item.name, item.description || `Articles from ${item.name}.`, query, {
    source_id: item.id,
    source_slug: item.slug,
    ...(type ? { content_type: type } : {}),
  });
}
export default async function Page({ params, searchParams }: Props) {
  const { id, contentType } = await params;
  const type = contentType ? contentTypeFromRoute(contentType) : undefined;
  if (contentType && !type) notFound();
  const query = await searchParams;
  const item = await load(id);
  const filters = {
    ...parseFilters({ ...query, ...(type ? { content_type: type } : {}), source_id: item.id }),
    source_slug: item.slug,
  };
  redirectAlias(id, item.slug, contentType, query);
  if ((!contentType && filters.content_type) || (contentType && "content_type" in query))
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({
    filters,
    title: item.name,
    description: item.description,
    logoUrl: item.logo_url,
    section: "sources",
  });
}
