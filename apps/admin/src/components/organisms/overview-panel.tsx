"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { OverviewLiveContext } from "./overview-live";
import { RetryButton } from "@devfeed/ui/retry-button";
import { adminOverviewPanel } from "@/lib/api/generated/admin";
import type { OverviewPanel as PanelData } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { overviewRequest } from "@/lib/overview-requests";
import { usePolling } from "@/lib/use-polling";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { notifyFailure } from "@/lib/notifications";
import { DateTime } from "@/components/molecules/date-time";

export type PanelStatus = {
  days: number;
  refresh: number;
  loading: boolean;
  failed: boolean;
  generatedAt?: string;
};
export type PanelName = Parameters<typeof adminOverviewPanel>[0];

export function OverviewPanel({
  panel,
  title,
  days,
  refresh,
  compact = false,
  children,
  onStatus,
}: {
  panel: PanelName;
  title: string;
  days: number;
  refresh: number;
  compact?: boolean;
  onStatus?: (panel: PanelName, status: PanelStatus) => void;
  children: (data: PanelData) => ReactNode;
}) {
  const [storedData, setData] = useState<PanelData>();
  const data = storedData?.days === days ? storedData : undefined;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  const stale = data && now - Date.parse(data.generated_at) > 5 * 60_000;
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const latestData = useRef<PanelData | undefined>(undefined);
  const request = useRef<AbortController | null>(null);
  const refreshSeconds = useRefreshInterval();
  const [checkedAt, setCheckedAt] = useState<number>();
  const load = useCallback(
    async (automaticSignal?: AbortSignal) => {
      if (automaticSignal && request.current) return;
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      const cancel = () => controller.abort();
      automaticSignal?.addEventListener("abort", cancel, { once: true });
      let failed = false;
      const report = (loading: boolean) =>
        onStatus?.(panel, {
          days,
          refresh,
          loading,
          failed,
          generatedAt:
            latestData.current?.days === days ? latestData.current.generated_at : undefined,
        });
      report(true);
      setLoading(true);
      setFailed(false);
      try {
        for (let attempt = 0; ; attempt++) {
          try {
            const next = await overviewRequest(controller.signal, () =>
              adminOverviewPanel(panel, { days }, { signal: controller.signal }),
            );
            if (!controller.signal.aborted) {
              setCheckedAt(Date.now());
              latestData.current = next;
              setData(next);
            }
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
        failed = true;
        setFailed(true);
        if (error instanceof ApiError && (error.status === 401 || error.status === 403))
          notifyFailure(error, `Could not load ${title}`);
      } finally {
        automaticSignal?.removeEventListener("abort", cancel);
        if (request.current === controller) {
          request.current = null;
          setLoading(false);
          report(false);
        }
      }
    },
    [panel, days, title, refresh, onStatus],
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
  usePolling((signal) => load(signal), refreshSeconds * 1000, `${panel}:${days}`);
  return (
    <section
      id={`overview-panel-${panel}`}
      aria-label={title}
      aria-busy={loading}
      data-overview-panel={panel}
      className="min-w-0 space-y-2"
    >
      {data ? (
        <div
          className={`overview-panel-content h-full ${["publication-automation", "job-tokens", "job-reliability", "workload"].includes(panel) ? "rounded-lg border bg-card p-5" : ""}`}
        >
          <OverviewLiveContext.Provider
            value={{
              checkedAt,
              failed,
              loading,
              interval: refreshSeconds,
            }}
          >
            {children(data)}
          </OverviewLiveContext.Provider>
        </div>
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
      {data && (failed || stale) && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          Stale data · Last successful result <DateTime value={data.generated_at} />
        </p>
      )}
    </section>
  );
}
