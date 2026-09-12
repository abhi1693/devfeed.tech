"use client";
import { LoadingSkeleton } from "./loading-skeleton";
import Link from "next/link";
import { useEffect, useState } from "react";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import type { FeedPage } from "@/lib/types";
import { AccountError, userRequest } from "@/lib/user";
import { AccountGate } from "./user-account";
import { InfiniteFeed } from "./infinite-feed";
import type { RecommendationReason } from "./article-grid";

type RecommendationPage = FeedPage & {
  status: "ready" | "refreshing";
  has_interests: boolean;
  reasons: Record<string, RecommendationReason>;
};

function Feed({ cursor }: { cursor?: string }) {
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    let polls = 0;
    let complete = false;
    return runWhenPageActive(signal => {
      if (complete) return;
      let timer: ReturnType<typeof setTimeout>;
      async function load() {
        if (signal.aborted) return;
        try {
          const result = await userRequest<RecommendationPage>(
            `feed?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
            { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) },
          );
          if (signal.aborted) return;
          setPage(result);
          if (result.status === "refreshing")
            timer = setTimeout(load, ++polls < 6 ? 3000 : 30000);
          else complete = true;
        } catch (cause) {
          if (!signal.aborted) {
            complete = true;
            setChanged(cause instanceof AccountError && cause.status === 409);
            setFailed(true);
          }
        }
      }
      void load();
      return () => clearTimeout(timer);
    });
  }, [cursor]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>My feed</h1>
          <p>Articles from your sources, topics, and likes.</p>
        </div>
        <div className="personal-feed-settings"><Link className="button" href="/settings/sources">Your sources</Link><Link className="button" href="/settings/topics">Your topics</Link></div>
      </div>
      {failed ? (
        <section className="empty-state">
          <h2>
            {changed ? "Your feed has been updated" : "Couldn’t load your feed"}
          </h2>
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- Retry reloads an already mounted route. */}
          <a className="button" href="/my-feed">
            {changed ? "Show updated feed" : "Try again"}
          </a>
        </section>
      ) : !page ? (
        <LoadingSkeleton label="Loading your feed…" />
      ) : page.status === "refreshing" ? (
        <section className="empty-state" role="status">
          <h2>Updating your feed</h2>
          <p>Your recommendations will appear here automatically.</p>
          <Link className="button" href="/">
            Browse latest articles
          </Link>
        </section>
      ) : page.items.length ? (
        <>
          <InfiniteFeed initialPage={page} personal />
          {cursor && (
            <div className="pagination">
              <Link className="button" href="/my-feed">Back to first page</Link>
            </div>
          )}
        </>
      ) : (
        <section className="empty-state">
          <h2>
            {cursor
              ? "You’re all caught up"
              : page.has_interests
                ? "No recommendations yet"
                : "Follow sources or topics to get started"}
          </h2>
          <p>
            {cursor
              ? "Return to the latest articles in your feed."
              : "Follow sources or topics, or like articles to shape your recommendations."}
          </p>
          <Link
            className="button primary"
            href={cursor ? "/my-feed" : "/settings/topics"}
          >
            {cursor ? "Back to first page" : "Choose topics"}
          </Link>
        </section>
      )}
    </>
  );
}
export function PersonalFeed({ cursor }: { cursor?: string }) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = () => setRevision((value) => value + 1);
    window.addEventListener("devfeed:interests-changed", changed);
    return () =>
      window.removeEventListener("devfeed:interests-changed", changed);
  }, []);
  return (
    <AccountGate>
      <Feed key={`${cursor ?? "latest"}/${revision}`} cursor={cursor} />
    </AccountGate>
  );
}
