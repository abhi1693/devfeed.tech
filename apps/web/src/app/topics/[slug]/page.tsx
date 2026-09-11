import { cache } from "react";
import { notFound } from "next/navigation";
import { getTopic, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ slug: string }>;
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
  const { slug } = await params;
  const item = await load(slug);
  const filters = parseFilters({ ...(await searchParams), topic: slug });
  return FeedView({
    filters,
    title: item.name,
    description: item.description,
    section: "topics",
  });
}
