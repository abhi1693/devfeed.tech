"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { RetryButton } from "@devfeed/ui/retry-button";
import { adminOverviewPanel } from "@/lib/api/generated/admin";
import type { OverviewPanel as PanelData } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { overviewRequest } from "@/lib/overview-requests";
import { usePolling } from "@/lib/use-polling";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { notifyFailure } from "@/lib/notifications";
import { DateTime } from "@/components/molecules/date-time";

export type PanelName = Parameters<typeof adminOverviewPanel>[0];

export function OverviewPanel({
  panel,
  title,
  days,
  refresh,
  compact = false,
  children,
}: {
  panel: PanelName;
  title: string;
  days: number;
  refresh: number;
  compact?: boolean;
  children: (data: PanelData) => ReactNode;
}) {
  const [storedData, setData] = useState<PanelData>();
  const data = storedData?.days === days ? storedData : undefined;
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const request = useRef<AbortController | null>(null);
  const refreshSeconds = useRefreshInterval();
  const load = useCallback(
    async (automaticSignal?: AbortSignal) => {
      if (automaticSignal && request.current) return;
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      const cancel = () => controller.abort();
      automaticSignal?.addEventListener("abort", cancel, { once: true });
      setLoading(true);
      setFailed(false);
      try {
        for (let attempt = 0; ; attempt++) {
          try {
            const next = await overviewRequest(controller.signal, () =>
              adminOverviewPanel(panel, { days }, { signal: controller.signal }),
            );
            if (!controller.signal.aborted) setData(next);
            break;
          } catch (error) {
            if (controller.signal.aborted) return;
            if (!(error instanceof ApiError) || error.status !== 503 || attempt >= 4) throw error;
            await new Promise<void>((resolve) => {
              const done = () => {
                clearTimeout(timer);
                controller.signal.removeEventListener("abort", done);
                resolve();
              };
              const timer = setTimeout(done, 2000);
              controller.signal.addEventListener("abort", done, { once: true });
            });
            controller.signal.throwIfAborted();
          }
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setFailed(true);
        if (error instanceof ApiError && (error.status === 401 || error.status === 403))
          notifyFailure(error, `Could not load ${title}`);
      } finally {
        automaticSignal?.removeEventListener("abort", cancel);
        if (request.current === controller) {
          request.current = null;
          setLoading(false);
        }
      }
    },
    [panel, days, title],
  );
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
      request.current?.abort();
    };
  }, [load]);
  const previousRefresh = useRef(refresh);
  useEffect(() => {
    if (previousRefresh.current !== refresh) {
      previousRefresh.current = refresh;
      void load();
    }
  }, [refresh, load]);
  usePolling((signal) => load(signal), refreshSeconds * 1000, `${panel}:${days}`);
  return (
    <section
      aria-label={title}
      aria-busy={loading}
      data-overview-panel={panel}
      className="min-w-0 space-y-2"
    >
      {data ? (
        children(data)
      ) : (
        <div
          role="status"
          aria-label={failed ? `Unavailable ${title}` : `Loading ${title}`}
          className={`${failed ? "" : "overview-shimmer"} relative overflow-hidden rounded-lg border bg-card p-5 ${compact ? "h-40" : "h-80"}`}
        >
          <span className="sr-only">
            {failed ? "Unavailable" : "Loading"} {title}
          </span>
          <div aria-hidden="true" className="space-y-6">
            <div className="h-4 w-2/3 rounded bg-muted" />
            <div className="h-7 w-1/3 rounded bg-muted" />
            <div className="h-2 w-full rounded bg-muted" />
            <div className="h-2 w-3/4 rounded bg-muted" />
          </div>
        </div>
      )}
      {failed && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm"
        >
          <span>
            Could not update {title.toLowerCase()}.
            {data ? " Showing the last successful result." : ""}
          </span>
          <RetryButton onRetry={() => void load()} pending={loading} />
        </div>
      )}
      {data && (
        <p className="text-right text-xs text-muted-foreground">
          {loading ? (
            "Updating…"
          ) : (
            <>
              Updated <DateTime value={data.generated_at} />
            </>
          )}
        </p>
      )}
    </section>
  );
}
