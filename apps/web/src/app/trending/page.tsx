import type { Metadata } from "next";
import { TrendingUp } from "lucide-react";
import { UserShell } from "@/components/user-shell";
import { InfiniteFeed } from "@/components/infinite-feed";
import type { SearchParams } from "@/lib/feed-query";
import { RetryFeed } from "@/components/retry-feed";
import { getTrending } from "@/lib/api";
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Trending articles",
  robots: { index: false, follow: false },
  description: "Developer articles users are opening and liking this week.",
};
export default async function Trending({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const cursor = typeof query.cursor === "string" ? query.cursor : "";
  const result = await Promise.allSettled([getTrending(cursor)]);
  const feed = result[0];
  return (
    <UserShell section="trending">
      <section className="feed-header">
        <div className="page-heading feed-heading">
          <h1>Trending</h1>
          <span className="sort-label">
            <TrendingUp size={15} />
            This week
          </span>
        </div>
        <p className="trending-description">
          Articles users are opening and liking.
        </p>
      </section>
      {feed.status === "rejected" ? (
        <section className="empty-state">
          <h2>Couldn’t load trending articles</h2>
          <RetryFeed />
        </section>
      ) : feed.value.items.length ? (
        <InfiniteFeed key={cursor} initialPage={feed.value} trending />
      ) : (
        <section className="empty-state">
          <h2>No trending articles yet</h2>
          <p>Popular articles will appear as users open and like them.</p>
        </section>
      )}
    </UserShell>
  );
}
