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
  if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id)) notFound();
  try {
    return await getSource(id);
  } catch (error) {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  }
});
export async function generateMetadata({ params, searchParams }: Props) {
  const item = await load((await params).id);
  return feedMetadata(
    item.name,
    item.description || `Articles from ${item.name}.`,
    await searchParams,
  );
}
export default async function Page({ params, searchParams }: Props) {
  const { id, contentType } = await params;
  const type = contentType ? contentTypeFromRoute(contentType) : undefined;
  if (contentType && !type) notFound();
  const query = await searchParams;
  const item = await load(id);
  const filters = parseFilters({
    ...query,
    ...(type ? { content_type: type } : {}),
    source_id: id,
  });
  if ((!contentType && filters.content_type) || (contentType && "content_type" in query))
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({
    filters,
    title: item.name,
    description: item.description,
    section: "sources",
  });
}
