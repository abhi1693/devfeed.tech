"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RetryButton } from "@devfeed/ui/retry-button";
import type { DateRange } from "@devfeed/ui/date-range-picker";
import { usePolling } from "@devfeed/ui/use-polling";
import { LoaderCircle } from "lucide-react";
import { ChartCard, MetricStat } from "@/components/molecules/overview-chart";
import { ChartPlaceholder } from "@/components/molecules/overview-chart-placeholder";
import { OverviewChartContent } from "./overview-chart-content";
import { chartDefinition, type ChartName } from "@/lib/overview-charts";
import { adminOverviewChart } from "@/lib/api/generated/admin";
import type { OverviewChartReport } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { overviewRequest } from "@/lib/overview-requests";
import { usePanelVisibility } from "@/lib/use-panel-visibility";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { readRequest, ReadRequestTimeout } from "@/lib/read-request";

/** Every card owns one bounded request, loading state, and refresh lifecycle. */
export function OverviewChart({ chart, range }: { chart: ChartName; range: DateRange }) {
  const definition = chartDefinition(chart);
  const { title } = definition;
  const { ref, near, visible } = usePanelVisibility();
  const from = chart === "attention" ? undefined : range.from;
  const to = chart === "attention" ? undefined : range.to;
  const key = chart === "attention" ? "attention:current" : `${chart}:${from ?? ""}:${to ?? ""}`;
  const [stored, setStored] = useState<{ key: string; report: OverviewChartReport }>();
  const [loadingKey, setLoadingKey] = useState<string>();
  const loading = loadingKey === key;
  const [failure, setError] = useState<{ key: string; message: string }>();
  const error = failure?.key === key ? failure.message : undefined;
  const [preparation, setPreparation] = useState<{
    key: string;
    startedAt: number;
    responses: number;
    delayed: boolean;
  }>();
  const preparing = preparation?.key === key ? preparation : undefined;
  const request = useRef<AbortController | null>(null);
  const loaded = useRef<string | null>(null);
  const interval = useRefreshInterval();
  const load = useCallback(
    async (automatic?: AbortSignal) => {
      if (automatic && request.current) return;
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      const cancel = () => controller.abort();
      automatic?.addEventListener("abort", cancel, { once: true });
      setLoadingKey(key);
      setError(undefined);
      const startedAt = Date.now();
      try {
        const result = await readRequest(controller.signal, (signal) =>
          overviewRequest(signal, () => adminOverviewChart(chart, { from, to }, { signal })),
        );
        if (!controller.signal.aborted) {
          if (result.chart !== chart) throw new Error("Unexpected chart response");
          loaded.current = key;
          setStored({ key, report: result });
          setPreparation(undefined);
        }
      } catch (failure) {
        if (controller.signal.aborted) return;
        if (failure instanceof ApiError && failure.code === "analytics_preparing") {
          setPreparation((previous) => {
            const next =
              previous?.key === key
                ? { ...previous, responses: previous.responses + 1 }
                : { key, startedAt, responses: 1, delayed: false };
            return {
              ...next,
              delayed: next.delayed || next.responses >= 3 || Date.now() - next.startedAt >= 30_000,
            };
          });
          return;
        }
        setPreparation(undefined);
        setError({
          key,
          message:
            failure instanceof ReadRequestTimeout
              ? `Loading ${title.toLowerCase()} took too long.`
              : `Could not update ${title.toLowerCase()}.`,
        });
      } finally {
        automatic?.removeEventListener("abort", cancel);
        if (request.current === controller) {
          request.current = null;
          setLoadingKey(undefined);
        }
      }
    },
    [chart, from, to, title, key],
  );
  useEffect(() => {
    if (!preparing || preparing.delayed) return;
    const timer = setTimeout(
      () => {
        setPreparation((current) =>
          current?.key === key ? { ...current, delayed: true } : current,
        );
      },
      Math.max(0, 30_000 - (Date.now() - preparing.startedAt)),
    );
    return () => clearTimeout(timer);
  }, [preparing, key]);
  useEffect(
    () => () => {
      request.current?.abort();
      request.current = null;
    },
    [load],
  );
  useEffect(() => {
    if (!near || loaded.current === key || request.current) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled && !request.current) void load();
    });
    return () => {
      cancelled = true;
    };
  }, [near, load, key]);
  const pollInterval = preparing
    ? preparing.delayed
      ? 15_000
      : 5000
    : chart === "queue-pressure"
      ? interval * 1000
      : 0;
  usePolling((signal) => load(signal), visible ? pollInterval : 0, key);
  const ready = stored?.key === key && !loading && !preparing && !error;
  const placeholder = ready ? null : (
    <>
      {chart === "publication-rate" && (
        <div className="flex items-end justify-between gap-4">
          <MetricStat value="—" label="published" />
          <MetricStat value="—" label="automatic" align="end" />
        </div>
      )}
      {chart === "publication-latency" && (
        <div className="flex items-end justify-between gap-4">
          <MetricStat value="—" label="median" />
          <MetricStat value="—" label="95th percentile" align="end" />
        </div>
      )}
      <div
        className={`flex items-center justify-center ${chart === "attention" ? "min-h-16" : "h-48"}`}
      >
        {error ? (
          <div role="alert" className="flex flex-col items-center gap-3 text-center text-sm">
            <p>{error}</p>
            <RetryButton onRetry={() => void load()} pending={loading} />
          </div>
        ) : (
          <div
            role="status"
            aria-label={`Loading ${title}`}
            className="flex flex-col items-center gap-3 text-sm text-muted-foreground"
          >
            <LoaderCircle aria-hidden className="size-5 animate-spin motion-reduce:animate-none" />
            {preparing?.delayed ? (
              <>
                <p>Still preparing this chart</p>
              </>
            ) : (
              <span className="sr-only">Loading {title}</span>
            )}
          </div>
        )}
      </div>
    </>
  );
  return (
    <section
      ref={ref}
      id={chart}
      data-overview-chart={chart}
      aria-label={title}
      aria-busy={loading || Boolean(preparing)}
      className={chart === "attention" ? "min-w-0 xl:col-span-2" : "min-w-0"}
    >
      <ChartPlaceholder value={placeholder}>
        {stored ? (
          <OverviewChartContent report={stored.report} onChange={() => void load()} />
        ) : (
          <ChartCard {...definition}>{null}</ChartCard>
        )}
      </ChartPlaceholder>
    </section>
  );
}
