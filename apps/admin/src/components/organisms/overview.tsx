"use client";

import { DateTime } from "@/components/molecules/date-time";

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert } from "lucide-react";
import { RetryButton } from "@devfeed/ui/retry-button";
import { Button } from "@/components/atoms/button";
import { OverviewCharts } from "@/components/organisms/overview-charts";
import { OverviewMetrics } from "./overview-metrics";
import { OverviewAttention, OverviewDetails, type OverviewSection } from "./overview-panels";
import { adminOverview } from "@/lib/api/generated/admin";
import type { AdminOverview } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { notify, notifyFailure } from "@/lib/notifications";
import { usePolling } from "@/lib/use-polling";
import { useRefreshInterval } from "@/lib/use-refresh-interval";

export function Overview({ initialData }: { initialData: AdminOverview }) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  function openSection(value: OverviewSection) {
    document
      .getElementById(
        { sources: "source-health", personalization: "personalization", operations: "processing" }[
          value
        ],
      )
      ?.scrollIntoView({ block: "start" });
  }
  const refreshSeconds = useRefreshInterval();
  const request = useRef<AbortController | null>(null);
  const requestedDays = useRef(initialData.days);
  const failureNotified = useRef(false);
  useEffect(() => () => request.current?.abort(), []);

  const refresh = useCallback(
    async (days: number, manual = false, automaticSignal?: AbortSignal) => {
      if (automaticSignal && request.current) return;
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      requestedDays.current = days;
      const cancel = () => {
        controller.abort();
        if (request.current === controller) {
          request.current = null;
          setLoading(false);
        }
      };
      automaticSignal?.addEventListener("abort", cancel, { once: true });
      setLoading(true);
      try {
        const next = await adminOverview({ days }, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setData(next);
        setFailed(false);
        failureNotified.current = false;
        if (manual) notify.success("Overview refreshed");
      } catch (error) {
        if (controller.signal.aborted) return;
        setFailed(true);
        if (
          manual ||
          !failureNotified.current ||
          (error instanceof ApiError && error.status === 401)
        ) {
          notifyFailure(error, "Could not refresh overview");
        }
        failureNotified.current = true;
      } finally {
        automaticSignal?.removeEventListener("abort", cancel);
        if (!controller.signal.aborted) {
          request.current = null;
          setLoading(false);
        }
      }
    },
    [],
  );

  usePolling((signal) => refresh(requestedDays.current, false, signal), refreshSeconds * 1000);

  return (
    <section className="space-y-6" aria-label="Application overview" aria-busy={loading}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        </div>
        <div
          className="flex rounded-lg border bg-muted/50 p-1"
          role="group"
          aria-label="Chart date range"
        >
          {[7, 30, 90].map((days) => (
            <Button
              key={days}
              variant={data.days === days ? "default" : "ghost"}
              size="sm"
              aria-pressed={data.days === days}
              disabled={loading}
              className="h-8 rounded-md px-3 text-xs"
              onClick={() => {
                if (days !== data.days) void refresh(days);
              }}
            >
              {days} days
            </Button>
          ))}
        </div>
      </div>
      {failed && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <CircleAlert aria-hidden className="size-4 shrink-0" />
          <span>Could not update the overview. Showing the last successful snapshot.</span>
          <RetryButton
            onRetry={() => void refresh(requestedDays.current, true)}
            pending={loading}
          />
        </div>
      )}
      <OverviewMetrics data={data} />
      <OverviewAttention data={data} onOpen={openSection} />
      <OverviewCharts data={data} />
      <OverviewDetails data={data} />
      <p className="text-right text-xs text-muted-foreground" role="status">
        {loading ? (
          "Updating overview…"
        ) : (
          <>
            Updated <DateTime value={data.generated_at} />
          </>
        )}
      </p>
    </section>
  );
}
