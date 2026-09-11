import { cache } from "react";
import { notFound } from "next/navigation";
import { getSource, UserApiError } from "@/lib/api";
import { FeedView } from "@/components/feed-view";
import { parseFilters, type SearchParams } from "@/lib/feed-query";
import { feedMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
type Props = {
  params: Promise<{ id: string }>;
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
  const { id } = await params;
  const item = await load(id);
  const filters = parseFilters({ ...(await searchParams), source_id: id });
  return FeedView({
    filters,
    title: item.name,
    description: item.description,
    section: "sources",
  });
}
