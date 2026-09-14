"use client";

import { createContext, useContext, useEffect, useState } from "react";

export const OverviewLiveContext = createContext<{
  checkedAt?: number;
  failed: boolean;
  loading: boolean;
  interval: number;
} | null>(null);

export function useWorkloadLive(generatedAt: string) {
  const state = useContext(OverviewLiveContext);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const stale = now - Date.parse(generatedAt) > 5 * 60_000;
  const live = !!state && state.interval > 0 && !state.failed && !stale;
  const label = !state
    ? "Snapshot"
    : state.failed
      ? state.interval
        ? "Reconnecting"
        : "Update failed"
      : stale
        ? "Stale data"
        : state.interval
          ? "Live"
          : "Manual refresh";
  const seconds =
    state?.checkedAt == null ? null : Math.max(0, Math.floor((now - state.checkedAt) / 1000));
  return {
    live,
    state,
    status: (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/20 px-3 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`size-2 rounded-full ${live ? "bg-[var(--chart-2)]" : "bg-muted-foreground"}`}
            aria-hidden="true"
          />
          <span className="font-medium" role="status" aria-label="Workload live status">
            {label}
          </span>
          <span className="text-muted-foreground">
            {state?.loading
              ? "Checking…"
              : seconds == null
                ? "No live check yet"
                : `Checked ${seconds}s ago`}
            {state && state.interval > 0 ? ` · every ${state.interval}s` : ""}
          </span>
        </div>
      </div>
    ),
  };
}
