"use client";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";
import Link from "next/link";
import { useEffect, useState } from "react";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import type { FeedPage } from "@/lib/types";
import { AccountError, userRequest } from "@/lib/user";
import { AccountGate } from "./user-account";
import { InfiniteFeed } from "./infinite-feed";
import { ReaderReloadLink } from "./reader-reload-link";
import type { RecommendationReason } from "./article-grid";

type RecommendationPage = FeedPage & {
  status: "ready" | "refreshing";
  generation?: string | null;
  has_interests: boolean;
  reasons: Record<string, RecommendationReason>;
};

function Feed({ cursor, revision }: { cursor?: string; revision: string }) {
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    let polls = 0;
    let complete = false;
    return runWhenPageActive((signal) => {
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
          setFailed(false);
          setChanged(false);
          setPage(result);
          if (result.status === "refreshing") timer = setTimeout(load, ++polls < 6 ? 3000 : 30000);
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
  }, [cursor, revision]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>My feed</h1>
          <p>Articles from your sources, topics, and likes.</p>
        </div>
        <div className="personal-feed-settings">
          <Link className="button" href="/settings/sources">
            Your sources
          </Link>
          <Link className="button" href="/settings/topics">
            Your topics
          </Link>
        </div>
      </div>
      <LoadingReveal
        loading={!page && !failed}
        fallback={<LoadingSkeleton label="Loading your feed…" />}
      >
        {failed && !page ? (
          <section className="empty-state">
            <h2>{changed ? "Your feed has been updated" : "Couldn’t load your feed"}</h2>
            <ReaderReloadLink href="/">
              {changed ? "Show updated feed" : "Try again"}
            </ReaderReloadLink>
          </section>
        ) : !page ? null : page.status === "refreshing" && !page.items.length ? (
          <section className="empty-state" role="status">
            <h2>Updating your feed</h2>
            <p>Your recommendations will appear here automatically.</p>
            <Link className="button" href="/latest">
              Browse latest articles
            </Link>
          </section>
        ) : page.items.length ? (
          <>
            {changed ? (
              <p role="status">
                <ReaderReloadLink href="/">Show updated feed</ReaderReloadLink>
              </p>
            ) : page.status === "refreshing" ? (
              <p role="status" className="text-muted-foreground">
                Updating recommendations in the background…
              </p>
            ) : null}
            <InfiniteFeed
              key={page.generation ?? JSON.stringify(page.items.map((item) => item.id))}
              initialPage={page}
              personal
            />
            {cursor && (
              <div className="pagination">
                <Link className="button" href="/">
                  Back to first page
                </Link>
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
            <Link className="button primary" href={cursor ? "/" : "/settings/topics"}>
              {cursor ? "Back to first page" : "Choose topics"}
            </Link>
          </section>
        )}
      </LoadingReveal>
    </>
  );
}
export function PersonalFeed({ cursor, refreshKey = 0 }: { cursor?: string; refreshKey?: number }) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = () => setRevision((value) => value + 1);
    window.addEventListener("devfeed:interests-changed", changed);
    return () => window.removeEventListener("devfeed:interests-changed", changed);
  }, []);
  return (
    <AccountGate>
      <Feed key={cursor ?? "latest"} cursor={cursor} revision={`${revision}:${refreshKey}`} />
    </AccountGate>
  );
}
