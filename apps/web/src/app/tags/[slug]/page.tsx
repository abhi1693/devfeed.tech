import { cache } from "react";
import { CollectionUnavailable } from "@/components/collection-unavailable";
import { headers } from "next/headers";
import { matchReaderDestinationPath } from "@devfeed/http/reader-destinations";
import { notFound, permanentRedirect } from "next/navigation";
import { getTag, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { collectionRoute } from "@/lib/reader-routes";
import { readerFeeds } from "@/lib/reader-feed";
import { collectionFeedMetadata } from "@/lib/server/collection-feed-metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ slug: string; contentType?: string }>;
  searchParams: Promise<SearchParams>;
};
const load = cache(async (slug: string, contentType?: string) => {
  // Client transitions can supply encoded params. Decode the request path once,
  // preserving literal percent sequences as well as punctuation and Unicode.
  const pathname = (await headers()).get("x-devfeed-reader-pathname");
  const matched = pathname ? matchReaderDestinationPath(pathname) : null;
  const resolvedSlug = matched?.destination.routeId.startsWith("tags-")
    ? matched.destination.parameters.slug
    : slug;
  const route = collectionRoute("tags", resolvedSlug, contentType);
  if (!route) notFound();
  try {
    return { route, tag: await getTag(route.slug) };
  } catch (error) {
    if (error instanceof UserApiError && error.status === 404) notFound();
    if (error instanceof UserApiError && error.status >= 500) return null;
    throw error;
  }
});
export async function generateMetadata({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const result = await load(slug, contentType);
  if (!result)
    return { title: "Page temporarily unavailable", robots: { index: false, follow: false } };
  const { route, tag } = result;
  return collectionFeedMetadata(
    tag.name,
    `Published developer articles tagged ${tag.name}.`,
    await searchParams,
    { tag: tag.slug, ...(route.contentType ? { content_type: route.contentType } : {}) },
  );
}
export default async function Page({ params, searchParams }: Props) {
  const { slug, contentType } = await params;
  const result = await load(slug, contentType);
  if (!result) return <CollectionUnavailable kind="tags" />;
  const { route, tag } = result;
  const query = await searchParams;
  const filters = parseFilters({
    ...query,
    ...(route.contentType ? { content_type: route.contentType } : {}),
    tag: route.slug,
  });
  if (
    (!route.contentType && filters.content_type) ||
    (route.contentType && "content_type" in query)
  )
    permanentRedirect(feedHref(filters, { cursor: filters.cursor }));
  return FeedView({
    filters,
    feedDescriptor: readerFeeds.contextual,
    title: tag.name,
    tagId: tag.id,
    section: "tags",
  });
}
