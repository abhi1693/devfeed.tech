import { redirect } from "next/navigation";
import { feedHref, parseFilters, type SearchParams } from "@/lib/feed-query";
import { hasUserSession } from "@/lib/api";
import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { PersonalFeed } from "@/components/personal-feed";
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "My feed",
  robots: { index: false, follow: false },
};
export default async function MyFeed({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const filters = parseFilters(query);
  if (
    filters.q ||
    filters.topic ||
    filters.source_id ||
    filters.tag ||
    filters.content_type ||
    filters.language
  )
    redirect(feedHref(filters, { cursor: filters.cursor }));
  const { cursor } = query;
  if (!(await hasUserSession())) redirect("/latest");
  return (
    <UserShell section="personal">
      <PersonalFeed cursor={typeof cursor === "string" ? cursor : undefined} />
    </UserShell>
  );
}
