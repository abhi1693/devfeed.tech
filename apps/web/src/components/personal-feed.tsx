"use client";
import { ExtensionInstallButton } from "./extension-install-button";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { FeedPage } from "@/lib/types";
import { AccountError, userRequest } from "@/lib/user";
import { AccountGate } from "./user-account";
import { InfiniteFeed } from "./infinite-feed";
import { ReaderReloadLink } from "./reader-reload-link";
import type { RecommendationReason } from "./article-grid";
import { FeedOnboarding } from "./feed-onboarding";

type RecommendationPage = FeedPage & {
  status: "ready" | "refreshing";
  generation?: string | null;
  has_interests: boolean;
  reasons: Record<string, RecommendationReason>;
};

function Feed({
  cursor,
  revision,
  refreshKey,
}: {
  cursor?: string;
  revision: number;
  refreshKey: number;
}) {
  const pinned = useRef<{ revision: number; generation?: string }>({ revision });
  const hasFeed = useRef(false);
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    // Once a feed is visible, preference changes must not replace this reading session.
    const interestsChanged = pinned.current.revision !== revision;
    pinned.current.revision = revision;
    if (hasFeed.current && interestsChanged) return;
    let polls = 0;
    // New tabs often leave focus in the address bar. Loading belongs to this
    // mounted feed, independently of the focus used to measure engagement.
    const controller = new AbortController();
    const { signal } = controller;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      if (signal.aborted) return;
      try {
        const result = await userRequest<RecommendationPage>(
          `feed?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : pinned.current.generation ? `&generation=${encodeURIComponent(pinned.current.generation)}` : ""}`,
          { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) },
        );
        if (signal.aborted) return;
        setFailed(false);
        setChanged(false);
        setPage(result);
        hasFeed.current = result.items.length > 0;
        if (result.items.length && result.generation) {
          pinned.current.generation = result.generation;
        }
        if (result.status === "refreshing") timer = setTimeout(load, ++polls < 6 ? 3000 : 30000);
      } catch (cause) {
        if (!signal.aborted) {
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
  }, [cursor, revision, refreshKey]);
  return (
    <>
      {!cursor && <FeedOnboarding />}
      <div className="page-heading">
        <div>
          <h1>My feed</h1>
          <p>Articles from your sources, topics, and likes.</p>
        </div>
        <div className="personal-feed-settings">
          <ExtensionInstallButton />
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
      <Feed key={cursor ?? "latest"} cursor={cursor} revision={revision} refreshKey={refreshKey} />
    </AccountGate>
  );
}
