import { cache } from "react";
import { guestFeedFilters, parseFilters, type SearchParams } from "@/lib/feed-query";
import { hasUserSession } from "@/lib/api";
import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { PersonalFeed } from "@/components/personal-feed";
import { FeedView } from "@/components/feed-view";
import { feedMetadata, SITE_DESCRIPTION } from "@/lib/metadata";

export const dynamic = "force-dynamic";
const homeSession = cache(hasUserSession);

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}): Promise<Metadata> {
  if (await homeSession()) return { title: "My feed", robots: { index: false, follow: false } };
  return feedMetadata("Developer news, tutorials & releases", SITE_DESCRIPTION, await searchParams);
}

export default async function MyFeed({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const { cursor } = query;
  if (!(await homeSession())) return FeedView({ filters: guestFeedFilters(query) });
  return (
    <UserShell section="personal">
      <PersonalFeed
        filters={parseFilters(query)}
        cursor={typeof cursor === "string" ? cursor : undefined}
      />
    </UserShell>
  );
}
