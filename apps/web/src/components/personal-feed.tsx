"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { FeedPage } from "@/lib/types";
import { userRequest } from "@/lib/user";
import { AccountGate } from "./user-account";
import { ArticleGrid } from "./article-grid";

function Feed({ cursor }: { cursor?: string }) {
  const [page, setPage] = useState<FeedPage | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    userRequest<FeedPage>(
      `feed?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      {
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(15000),
        ]),
      },
    )
      .then(setPage)
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [cursor]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>My feed</h1>
          <p>Latest articles from your followed topics.</p>
        </div>
        <Link className="button" href="/preferences">
          Edit topics
        </Link>
      </div>
      {failed ? (
        <section className="empty-state">
          <h2>Couldn’t load your feed</h2>
          <Link className="button" href="/my-feed">
            Try again
          </Link>
        </section>
      ) : !page ? (
        <p role="status">Loading your feed…</p>
      ) : page.items.length ? (
        <>
          <ArticleGrid articles={page.items} />
          <div className="pagination">
            {cursor && (
              <Link className="button" href="/my-feed">
                Back to latest
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
            {cursor ? "You’re all caught up" : "Choose topics to get started"}
          </h2>
          <p>
            {cursor
              ? "Return to the latest articles in your feed."
              : "Articles from your followed topics will appear here as they’re published."}
          </p>
          <Link
            className="button primary"
            href={cursor ? "/my-feed" : "/preferences"}
          >
            {cursor ? "Back to latest" : "Choose topics"}
          </Link>
        </section>
      )}
    </>
  );
}
export function PersonalFeed({ cursor }: { cursor?: string }) {
  return (
    <AccountGate>
      <Feed key={cursor ?? "latest"} cursor={cursor} />
    </AccountGate>
  );
}
