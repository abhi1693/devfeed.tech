import { TrendingUp } from "lucide-react";
import { UserShell } from "./user-shell";
import { InfiniteFeed } from "./infinite-feed";
import { FeedViewToggle } from "./feed-view-toggle";
import { LoadingSkeleton } from "./loading-skeleton";
import { RetryFeed } from "./retry-feed";
import type { FeedPage } from "@/lib/types";

export function TrendingContent({
  feed,
  cursor = "",
}: {
  feed?: PromiseSettledResult<FeedPage>;
  cursor?: string;
}) {
  return (
    <UserShell section="trending">
      <section className="feed-header">
        <div className="page-heading feed-heading">
          <h1>Trending</h1>
          <div className="feed-toolbar-actions">
            <span className="sort-label">
              <TrendingUp size={15} />
              This week
            </span>
            <FeedViewToggle />
          </div>
        </div>
        <p className="trending-description">Articles users are opening and liking.</p>
      </section>
      {!feed ? (
        <LoadingSkeleton label="Loading trending articles…" />
      ) : feed.status === "rejected" ? (
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
