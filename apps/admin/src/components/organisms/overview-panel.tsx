"use client";

import { Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { OverviewLiveContext } from "./overview-live";
import { RetryButton } from "@devfeed/ui/retry-button";
import { adminOverviewPanel } from "@/lib/api/generated/admin";
import type { OverviewPanel as PanelData } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { overviewRequest } from "@/lib/overview-requests";
import { usePanelVisibility } from "@/lib/use-panel-visibility";
import { usePolling } from "@devfeed/ui/use-polling";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { notifyFailure } from "@/lib/notifications";
import { DateTime } from "@/components/molecules/date-time";
import { SnapshotStatus } from "@/components/molecules/snapshot-status";
import { readRequest, ReadRequestTimeout } from "@/lib/read-request";

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
  const { ref, near, visible } = usePanelVisibility();
  const [storedData, setData] = useState<PanelData>();
  const data = storedData?.days === days ? storedData : undefined;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!visible) return;
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, [visible]);
  const stale = data && now - Date.parse(data.generated_at) > 5 * 60_000;
  const [failed, setFailed] = useState(false);
  const [awaitingReport, setAwaitingReport] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [loading, setLoading] = useState(false);
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
      setLoading(true);
      setFailed(false);
      setAwaitingReport(false);
      setTimedOut(false);
      try {
        const next = await readRequest(controller.signal, (signal) =>
          overviewRequest(signal, () => adminOverviewPanel(panel, { days }, { signal })),
        );
        if (!controller.signal.aborted) {
          setCheckedAt(Date.now());
          setData(next);
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setFailed(true);
        setTimedOut(error instanceof ReadRequestTimeout);
        setAwaitingReport(
          error instanceof ApiError &&
            ["report_refreshing", "report_rebuilding"].includes(error.code ?? ""),
        );
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
    if (!near) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
      request.current?.abort();
    };
  }, [load, refresh, near]);
  usePolling((signal) => load(signal), visible ? refreshSeconds * 1000 : 0, `${panel}:${days}`);
  return (
    <section
      ref={ref}
      style={{
        contentVisibility: "auto",
        containIntrinsicSize: compact ? "auto 160px" : "auto 320px",
      }}
      id={`overview-panel-${panel}`}
      aria-label={title}
      aria-busy={loading}
      data-overview-panel={panel}
      className="flex min-w-0 flex-col gap-2"
    >
      {data ? (
        <div
          className={`overview-panel-content flex-1 ${["publication-automation", "job-tokens", "job-reliability", "workload"].includes(panel) ? "rounded-lg border bg-card p-5" : ""}`}
        >
          <OverviewLiveContext.Provider
            value={{
              checkedAt,
              failed,
              loading,
              interval: refreshSeconds,
              active: visible,
            }}
          >
            <Suspense
              fallback={
                <div className={compact ? "h-40" : "h-80"} role="status">
                  Loading {title}
                </div>
              }
            >
              {children(data)}
            </Suspense>
          </OverviewLiveContext.Provider>
        </div>
      ) : (
        <div
          role="status"
          aria-label={
            awaitingReport
              ? `Preparing ${title}`
              : failed
                ? `Unavailable ${title}`
                : `Loading ${title}`
          }
          className={`${!failed && near ? "overview-shimmer" : ""} relative overflow-hidden rounded-lg border bg-card p-5 ${compact ? "h-40" : "h-80"}`}
        >
          <span className="sr-only">
            {awaitingReport ? "Preparing" : failed ? "Unavailable" : "Loading"} {title}
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
            {awaitingReport
              ? `The saved ${title.toLowerCase()} report is being prepared.`
              : timedOut
                ? `Loading ${title.toLowerCase()} took too long. Please retry.`
                : `Could not update ${title.toLowerCase()}.`}
            {data ? " Showing the last successful result." : ""}
          </span>
          <RetryButton onRetry={() => void load()} pending={loading} />
        </div>
      )}
      {data?.snapshot && (
        <SnapshotStatus snapshot={data.snapshot} maxAgeSeconds={300} active={visible} />
      )}
      {data && !data.snapshot && (failed || stale) && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          Stale data · Last successful result <DateTime value={data.generated_at} />
        </p>
      )}
    </section>
  );
}
