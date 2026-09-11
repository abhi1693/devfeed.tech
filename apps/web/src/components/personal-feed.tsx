"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { FeedPage } from "@/lib/types";
import { AccountError, userRequest } from "@/lib/user";
import { AccountGate } from "./user-account";
import { ArticleGrid } from "./article-grid";

type RecommendationPage = FeedPage & {
  status: "ready" | "refreshing";
  has_interests: boolean;
  reasons: Record<
    string,
    {
      kind: "followed_topic" | "liked_topic" | "related_topic";
      topic_id: string;
      seed_topic_id: string;
    }
  >;
};

function Feed({ cursor }: { cursor?: string }) {
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let polls = 0;
    async function load() {
      if (document.hidden) {
        timer = setTimeout(load, 30000);
        return;
      }
      try {
        const result = await userRequest<RecommendationPage>(
          `feed?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
          {
            signal: AbortSignal.any([
              controller.signal,
              AbortSignal.timeout(15000),
            ]),
          },
        );
        if (controller.signal.aborted) return;
        setPage(result);
        if (result.status === "refreshing")
          timer = setTimeout(load, ++polls < 6 ? 3000 : 30000);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setChanged(cause instanceof AccountError && cause.status === 409);
          setFailed(true);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [cursor]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>My feed</h1>
          <p>Articles picked from your topics and likes.</p>
        </div>
        <Link className="button" href="/preferences">
          Edit topics
        </Link>
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
        <p role="status">Loading your feed…</p>
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
          <ArticleGrid articles={page.items} reasons={page.reasons} />
          <div className="pagination">
            {cursor && (
              <Link className="button" href="/my-feed">
                Back to first page
              </Link>
            )}
            {page.next_cursor && (
              <Link
                className="button primary"
                href={`/my-feed?cursor=${encodeURIComponent(page.next_cursor)}`}
              >
                More articles
              </Link>
            )}
          </div>
        </>
      ) : (
        <section className="empty-state">
          <h2>
            {cursor
              ? "You’re all caught up"
              : page.has_interests
                ? "No recommendations yet"
                : "Choose topics to get started"}
          </h2>
          <p>
            {cursor
              ? "Return to the latest articles in your feed."
              : "Follow topics or like articles to shape your recommendations."}
          </p>
          <Link
            className="button primary"
            href={cursor ? "/my-feed" : "/preferences"}
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
