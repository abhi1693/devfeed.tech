"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { FeedPage } from "@/lib/types";
import { userRequest } from "@/lib/user";
import { AccountGate, useUser } from "./user-account";
import { bookmarkChanged, type BookmarkChange } from "./article-engagement";
import { InfiniteFeed } from "./infinite-feed";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";

function SavedArticles({ cursor }: { cursor?: string }) {
  const { user } = useUser();
  const [page, setPage] = useState<FeedPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [excludedIds, setExcludedIds] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    userRequest<FeedPage>(
      `bookmarks?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) },
    )
      .then((value) => {
        if (!controller.signal.aborted) {
          setPage(value);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [cursor, attempt]);
  useEffect(() => {
    const update = (event: Event) => {
      const value = (event as CustomEvent<BookmarkChange>).detail;
      if (value.owner !== user?.user_id) return;
      setExcludedIds((ids) =>
        value.bookmarked
          ? ids.filter((id) => id !== value.article_id)
          : [...new Set([...ids, value.article_id])],
      );
    };
    window.addEventListener(bookmarkChanged, update);
    return () => window.removeEventListener(bookmarkChanged, update);
  }, [user?.user_id]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Read later</h1>
          <p>Your saved articles, newest first.</p>
        </div>
      </div>
      <LoadingReveal
        loading={!page && !failed}
        fallback={<LoadingSkeleton label="Loading saved articles…" />}
      >
        {failed ? (
          <section className="empty-state" role="alert">
            <h2>Couldn’t load saved articles</h2>
            <button
              className="button"
              onClick={() => {
                setFailed(false);
                setAttempt((a) => a + 1);
              }}
            >
              Try again
            </button>
          </section>
        ) : (
          page && <InfiniteFeed initialPage={page} bookmarks excludedIds={excludedIds} />
        )}
      </LoadingReveal>
      <div className="pagination">
        <Link className="button" href={cursor ? "/read-later" : "/"}>
          {cursor ? "Latest saved articles" : "Browse articles"}
        </Link>
      </div>
    </>
  );
}
export function ReadLater({ cursor }: { cursor?: string }) {
  const { user } = useUser();
  return (
    <AccountGate
      returnTo="/read-later"
      title="Keep articles for later"
      description="Sign in to save articles and read them on any of your devices."
    >
      <SavedArticles key={`${user?.user_id}/${cursor ?? "latest"}`} cursor={cursor} />
    </AccountGate>
  );
}
