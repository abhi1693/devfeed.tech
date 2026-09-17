"use client";

import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";
import { runWhenPageActive } from "@devfeed/ui/page-activity";

type Props = {
  children: ReactNode;
  hasMore: boolean;
  loading: boolean;
  error: boolean;
  onLoadMore: () => Promise<unknown>;
  label: string;
  nextHref?: string;
  showMore?: boolean;
  endMessage?: string;
  errorMessage?: string;
  recovery?: ReactNode;
  autoLoad?: boolean;
};

/** Shared scroll boundary with an accessible manual/navigation fallback. */
export function InfiniteScroll({
  children,
  hasMore,
  loading,
  error,
  onLoadMore,
  label,
  nextHref,
  showMore = true,
  endMessage = "You’re all caught up.",
  errorMessage,
  recovery,
  autoLoad = true,
}: Props) {
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!autoLoad || !hasMore || loading || error || !sentinel.current) return;
    return runWhenPageActive((signal) => {
      if (!globalThis.IntersectionObserver) {
        const checkBoundary = () => {
          const bounds = sentinel.current?.getBoundingClientRect();
          if (
            !signal.aborted &&
            bounds &&
            bounds.top <= window.innerHeight + 600 &&
            bounds.bottom >= -600
          )
            void onLoadMore();
        };
        window.addEventListener("scroll", checkBoundary, { passive: true });
        window.addEventListener("resize", checkBoundary);
        checkBoundary();
        return () => {
          window.removeEventListener("scroll", checkBoundary);
          window.removeEventListener("resize", checkBoundary);
        };
      }
      const observer = new IntersectionObserver(
        (entries) => {
          if (!signal.aborted && entries.some((entry) => entry.isIntersecting)) void onLoadMore();
        },
        { rootMargin: "600px 0px" },
      );
      if (sentinel.current) observer.observe(sentinel.current);
      return () => observer.disconnect();
    });
  }, [autoLoad, hasMore, loading, error, onLoadMore]);

  return (
    <>
      {children}
      <div ref={sentinel} className="pagination" aria-busy={loading} data-has-more={hasMore}>
        <p role="status">
          {loading
            ? `Loading more ${label}…`
            : error
              ? (errorMessage ?? `Couldn’t load more ${label}.`)
              : !hasMore
                ? endMessage
                : ""}
        </p>
        {recovery ??
          (hasMore &&
            !loading &&
            (error || showMore) &&
            (nextHref ? (
              <Link
                className="button"
                href={nextHref}
                prefetch={false}
                onClick={(event) => {
                  if (
                    event.button !== 0 ||
                    event.metaKey ||
                    event.ctrlKey ||
                    event.shiftKey ||
                    event.altKey
                  )
                    return;
                  event.preventDefault();
                  void onLoadMore();
                }}
              >
                {error ? "Try again" : `More ${label}`}
              </Link>
            ) : (
              <button className="button" type="button" onClick={() => void onLoadMore()}>
                {error ? "Try again" : `More ${label}`}
              </button>
            )))}
      </div>
    </>
  );
}
