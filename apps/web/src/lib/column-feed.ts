import type { RecommendationReason } from "@/components/article-grid";
import type { FeedPage } from "./types";

export const columnFeedLimits = {
  concurrentRequests: 3,
  pageSize: 24,
  retainedPages: 3,
  articlesPerColumn: 72,
} as const;

export type ColumnPage = FeedPage & {
  status?: "ready" | "refreshing";
  has_interests?: boolean;
  feed_kind?: "personalized" | "following" | "latest";
  refresh_state?: "idle" | "preparing" | "retrying";
  reasons?: Record<string, RecommendationReason>;
};

export type ColumnRequestQueue = {
  run: <T>(task: (signal: AbortSignal) => Promise<T>, signal: AbortSignal) => Promise<T>;
};

/** A workspace shares its request budget, including selection lookups and pagination. */
export function createColumnRequestQueue(): ColumnRequestQueue {
  let active = 0;
  const waiting: { start: () => void }[] = [];
  const pump = () => {
    while (active < columnFeedLimits.concurrentRequests && waiting.length) waiting.shift()!.start();
  };
  return {
    run: (task, signal) =>
      new Promise((resolve, reject) => {
        const abort = () => reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
        if (signal.aborted) return abort();
        const cancel = () => {
          const index = waiting.indexOf(job);
          if (index !== -1) waiting.splice(index, 1);
          abort();
        };
        const job = {
          start: () => {
            signal.removeEventListener("abort", cancel);
            if (signal.aborted) return abort();
            active++;
            // Start the deadline when transport begins, rather than while queued.
            const deadline = AbortSignal.any([signal, AbortSignal.timeout(15000)]);
            void Promise.resolve()
              .then(() => task(deadline))
              .then(resolve, reject)
              .finally(() => {
                // A transport ignoring cancellation still occupies its slot until settled.
                active--;
                pump();
              });
          },
        };
        signal.addEventListener("abort", cancel, { once: true });
        waiting.push(job);
        pump();
      }),
  };
}
