import type { Metadata } from "next";
import { TrendingUp } from "lucide-react";
import { UserShell } from "@/components/user-shell";
import { ArticleGrid } from "@/components/article-grid";
import { RetryFeed } from "@/components/retry-feed";
import { getTrending } from "@/lib/api";
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Trending articles",
  robots: { index: false, follow: false },
  description: "Developer articles users are opening and liking this week.",
};
export default async function Trending() {
  const result = await Promise.allSettled([getTrending()]);
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
        <ArticleGrid articles={feed.value.items} />
      ) : (
        <section className="empty-state">
          <h2>No trending articles yet</h2>
          <p>Popular articles will appear as users open and like them.</p>
        </section>
      )}
    </UserShell>
  );
}
