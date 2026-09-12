import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { getTag, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { contentTypeFromRoute, feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
import { publicSiteOrigin } from "@/lib/server/config";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ slug: string; contentType?: string }>;
  searchParams: Promise<SearchParams>;
};
const load = cache(async (slug: string) => {
  if (!slug || slug.length > 100) notFound();
  try {
    return await getTag(slug);
  } catch (error) {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  }
});
export async function generateMetadata({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const tag = await load(slug);
  return {
    ...feedMetadata(
      tag.name,
      `Published developer articles tagged ${tag.name}.`,
      await searchParams,
    ),
    alternates: {
      canonical: `${publicSiteOrigin()}/tags/${encodeURIComponent(tag.slug)}${contentType ? `/${encodeURIComponent(contentType)}` : ""}`,
    },
  };
}
export default async function Page({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const type = contentType ? contentTypeFromRoute(contentType) : undefined;
  if (contentType && !type) notFound();
  const tag = await load(slug);
  const query = await searchParams;
  const filters = parseFilters({ ...query, ...(type ? { content_type: type } : {}), tag: slug });
  if ((!contentType && filters.content_type) || (contentType && "content_type" in query))
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({ filters, title: tag.name, description: `Articles tagged ${tag.name}.` });
}
