"use client";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FeedPage } from "@/lib/types";
import { readerRequest } from "@/lib/reader-runtime";
import { feedParams, parseFilters, personalFeedHref, type FeedFilters } from "@/lib/feed-query";
import { FeedFiltersBar } from "./feed-filters";
import type { FeedOptions } from "@/lib/types";
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
  feed_kind?: "personalized" | "following" | "latest";
  refresh_state?: "idle" | "preparing" | "retrying";
};

function Feed({
  cursor,
  revision,
  refreshKey,
  filters,
}: {
  cursor?: string;
  revision: number;
  refreshKey: number;
  filters: FeedFilters;
}) {
  const [options, setOptions] = useState<FeedOptions | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void readerRequest(`/api/v1/feed/options?${feedParams({ ...filters, cursor: "", sort: "" })}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Options unavailable");
        const value = (await response.json()) as FeedOptions;
        if (!controller.signal.aborted) setOptions(value);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [filters]);
  const pinned = useRef<{ revision: number; generation?: string }>({ revision });
  const hasFeed = useRef(false);
  const preparing = useRef(false);
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [changed, setChanged] = useState(false);
  const [available, setAvailable] = useState<RecommendationPage | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [slow, setSlow] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retryVersion, setRetryVersion] = useState(0);
  useEffect(() => {
    // Once a feed is visible, preference changes must not replace this reading session.
    const interestsChanged = pinned.current.revision !== revision;
    pinned.current.revision = revision;
    if (hasFeed.current && interestsChanged && !preparing.current) return;
    const controller = new AbortController();
    const { signal } = controller;
    let timer: ReturnType<typeof setTimeout>;
    let slowTimer: ReturnType<typeof setTimeout>;
    let inFlight = false;
    let pending = true;
    let checks = 0;
    let waitingSince: number | undefined;
    async function load(initial = false) {
      if (signal.aborted || inFlight || (!initial && document.visibilityState === "hidden")) return;
      inFlight = true;
      try {
        // Initial/session refreshes retain the reading generation. Preparation
        // checks request the latest generation without replacing the visible list.
        const result = await userRequest<RecommendationPage>(
          `feed?limit=24&${feedParams({ ...filters, cursor: "" })}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : initial && retryVersion === 0 && pinned.current.generation ? `&generation=${encodeURIComponent(pinned.current.generation)}` : ""}`,
          { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) },
        );
        if (signal.aborted) return;
        setFailed(false);
        setChanged(false);
        pending = result.status === "refreshing";
        preparing.current = pending;
        setRefreshing(pending);
        setRetrying(result.refresh_state === "retrying");
        if (pending && waitingSince === undefined) {
          waitingSince = Date.now();
          slowTimer = setTimeout(() => setSlow(true), 10000);
        }
        if (!pending) {
          clearTimeout(slowTimer);
          setSlow(false);
        }
        if (!hasFeed.current) {
          setPage(result);
          hasFeed.current = result.items.length > 0;
          if (result.items.length && result.generation)
            pinned.current.generation = result.generation;
        } else if (!pending && (!initial || result.generation !== pinned.current.generation)) {
          // Readers opt into a new list; never shuffle articles underneath them.
          setAvailable(result);
        }
      } catch (cause) {
        if (!signal.aborted) {
          const expired = cause instanceof AccountError && cause.status === 409;
          setChanged(expired);
          setFailed(true);
          setRefreshing(false);
          pending = !expired;
          preparing.current = pending;
        }
      } finally {
        inFlight = false;
        if (!signal.aborted && pending && document.visibilityState !== "hidden")
          timer = setTimeout(() => void load(), ++checks < 6 ? 3000 : 5000);
      }
    }
    const visibility = () => {
      clearTimeout(timer);
      if (pending && document.visibilityState !== "hidden") void load();
    };
    document.addEventListener("visibilitychange", visibility);
    // A new tab may leave focus in the omnibox; always allow its initial load.
    void load(true);
    return () => {
      controller.abort();
      clearTimeout(timer);
      clearTimeout(slowTimer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [cursor, revision, refreshKey, filters, retryVersion]);
  function showUpdates() {
    if (!available) return;
    setPage(available);
    pinned.current.generation = available.generation ?? undefined;
    hasFeed.current = available.items.length > 0;
    setAvailable(null);
  }
  const preparation = (
    <section className="feed-preparation" aria-label="Feed preparation">
      <div role="status" aria-live="polite">
        <strong>
          {available
            ? "Your feed is ready"
            : failed
              ? "We couldn’t check your recommendations"
              : slow || retrying
                ? "Your recommendations are taking longer than usual"
                : "Finding articles for you"}
        </strong>
        <p>
          {available
            ? "Your personalized articles are ready when you are."
            : failed
              ? "You can keep reading. We’ll try again shortly."
              : page?.items.length
                ? "Keep reading—we’ll let you know here when your recommendations are ready."
                : "We’re preparing recommendations from your topics and sources. You can browse the latest articles while we work."}
        </p>
      </div>
      {available ? (
        <button type="button" className="button primary" onClick={showUpdates}>
          Show updates
        </button>
      ) : failed ? (
        <button
          type="button"
          className="button"
          onClick={() => setRetryVersion((value) => value + 1)}
        >
          Check again
        </button>
      ) : null}
      <Link className="button" href="/latest">
        Browse latest articles
      </Link>
    </section>
  );
  return (
    <>
      {!cursor && <FeedOnboarding />}
      <section className="feed-header" aria-label="Feed controls">
        <FeedFiltersBar
          filters={filters}
          sources={options?.sources ?? []}
          availableTypes={options?.content_types}
          personal
        />
      </section>
      <LoadingReveal
        loading={!page && !failed}
        fallback={<LoadingSkeleton label="Loading your feed…" />}
      >
        {failed && !page ? (
          <section className="empty-state">
            <h2>{changed ? "Your feed has been updated" : "Couldn’t load your feed"}</h2>
            <ReaderReloadLink href={personalFeedHref(filters)}>
              {changed ? "Show updated feed" : "Try again"}
            </ReaderReloadLink>
          </section>
        ) : !page ? null : (refreshing || available || failed) && !page.items.length ? (
          preparation
        ) : page.items.length ? (
          <>
            {changed ? (
              <p role="status">
                <ReaderReloadLink href={personalFeedHref(filters)}>
                  Show updated feed
                </ReaderReloadLink>
              </p>
            ) : refreshing || available || failed ? (
              preparation
            ) : null}
            {page.feed_kind && page.feed_kind !== "personalized" && (
              <h2 className="starter-feed-heading">
                {page.feed_kind === "following"
                  ? "Recent articles from your follows"
                  : "Latest articles while we prepare your feed"}
              </h2>
            )}
            <InfiniteFeed
              key={`${page.feed_kind ?? "personalized"}:${page.generation ?? JSON.stringify(page.items.map((item) => item.id))}`}
              initialPage={page}
              endMessage={page.feed_kind && page.feed_kind !== "personalized" ? "" : undefined}
              personal
              filters={filters}
            />
            {cursor && (
              <div className="pagination">
                <Link className="button" href={personalFeedHref(filters)}>
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
            <Link
              className="button primary"
              href={cursor ? personalFeedHref(filters) : "/settings/topics"}
            >
              {cursor ? "Back to first page" : "Choose topics"}
            </Link>
          </section>
        )}
      </LoadingReveal>
    </>
  );
}
const defaultFilters = parseFilters({});
export function PersonalFeed({
  cursor,
  refreshKey = 0,
  filters = defaultFilters,
}: {
  cursor?: string;
  refreshKey?: number;
  filters?: FeedFilters;
}) {
  const filterKey = JSON.stringify(filters);
  const stableFilters = useMemo(() => JSON.parse(filterKey) as FeedFilters, [filterKey]);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = () => setRevision((value) => value + 1);
    window.addEventListener("devfeed:interests-changed", changed);
    return () => window.removeEventListener("devfeed:interests-changed", changed);
  }, []);
  return (
    <AccountGate>
      <Feed
        key={JSON.stringify({ ...filters, cursor })}
        cursor={cursor}
        revision={revision}
        refreshKey={refreshKey}
        filters={stableFilters}
      />
    </AccountGate>
  );
}
