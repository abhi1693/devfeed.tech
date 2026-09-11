import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { getTopic, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { contentTypeFromRoute, feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ slug: string; contentType?: string }>;
  searchParams: Promise<SearchParams>;
};
const load = cache(async (slug: string) => {
  if (!slug || slug.length > 100) notFound();
  try {
    return await getTopic(slug);
  } catch (error) {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  }
});
export async function generateMetadata({ params, searchParams }: Props) {
  const item = await load((await params).slug);
  return feedMetadata(
    item.name,
    item.description || `Articles about ${item.name}.`,
    await searchParams,
  );
}
export default async function Page({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const type = contentType ? contentTypeFromRoute(contentType) : undefined;
  if (contentType && !type) notFound();
  const query = await searchParams;
  const item = await load(slug);
  const filters = parseFilters({ ...query, ...(type ? { content_type: type } : {}), topic: slug });
  if ((!contentType && filters.content_type) || (contentType && "content_type" in query))
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({
    filters,
    title: item.name,
    logoUrl: item.logo_url,
    description: item.description,
    section: "topics",
  });
}
