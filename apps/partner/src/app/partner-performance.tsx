"use client";

import { useState } from "react";
import Link from "next/link";
import { Download, Eye, MousePointerClick, TrendingUp } from "lucide-react";
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip } from "@devfeed/ui/chart";
import { Button } from "@devfeed/ui/button";
import { adFormatLabel } from "@devfeed/ui/advertising-formats";
import type { WorkspaceDashboard } from "@/lib/api/generated/models";
import { portalPath } from "@/lib/routes";
import {
  metricCount,
  metricPercent,
  performanceCsv,
  performanceDays,
  shortMetricDate,
  type PerformanceDay,
} from "@/lib/performance";

function Metrics({ data }: { data: WorkspaceDashboard }) {
  const measured = data.totals.measured_days > 0;
  const metrics = [
    {
      label: "Impressions",
      value: measured ? metricCount(data.totals.impressions) : "—",
      hint: "Recorded product and ad views",
      icon: Eye,
    },
    {
      label: "Clicks",
      value: measured ? metricCount(data.totals.clicks) : "—",
      hint: "Visits to your destinations",
      icon: MousePointerClick,
    },
    {
      label: "Click-through rate",
      value: data.totals.ctr === null ? "—" : metricPercent(data.totals.ctr),
      hint: "Total clicks ÷ total impressions",
      icon: TrendingUp,
    },
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {metrics.map(({ label, value, hint, icon: Icon }) => (
        <section key={label} aria-label={label} className="rounded-lg border bg-card p-5">
          <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
            <h3 className="font-medium">{label}</h3>
            <Icon size={16} aria-hidden="true" />
          </div>
          <p className="mt-3 text-3xl font-semibold tracking-tight tabular-nums">{value}</p>
          <p className="mt-2 text-xs text-muted-foreground">{hint}</p>
        </section>
      ))}
    </div>
  );
}

function TrendChart({
  rows,
  metric,
  label,
}: {
  rows: PerformanceDay[];
  metric: "impressions" | "clicks" | "ctr";
  label: string;
}) {
  const percentage = metric === "ctr";
  const format = percentage ? metricPercent : metricCount;
  if (!rows.some((row) => row[metric] !== null))
    return (
      <p className="flex h-64 items-center justify-center px-5 text-center text-sm text-muted-foreground">
        {percentage
          ? "Click-through rate appears after the first measured impression."
          : "Your delivery trend will appear when measurements arrive."}
      </p>
    );
  return (
    <ChartContainer label={label} className="overflow-hidden">
      <LineChart data={rows} accessibilityLayer margin={{ top: 10, right: 16, bottom: 5, left: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
        <XAxis
          dataKey="day"
          tickFormatter={shortMetricDate}
          tickLine={false}
          axisLine={false}
          minTickGap={40}
          tickMargin={10}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          allowDecimals={percentage}
          tickFormatter={
            percentage
              ? (value: number) => `${value.toLocaleString("en", { maximumFractionDigits: 1 })}%`
              : (value: number) => value.toLocaleString("en", { notation: "compact" })
          }
          domain={[0, "auto"]}
          width={58}
        />
        <Tooltip
          content={(props) => (
            <ChartTooltip {...props} formatLabel={shortMetricDate} formatValue={format} />
          )}
        />
        <Line
          type="linear"
          dataKey={metric}
          name={label}
          stroke={percentage ? "var(--chart-2)" : "var(--chart-1)"}
          strokeWidth={2}
          dot={{ r: 2 }}
          activeDot={{ r: 4 }}
          connectNulls={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ChartContainer>
  );
}

function DeliveryTrend({ rows }: { rows: PerformanceDay[] }) {
  const [metric, setMetric] = useState<"impressions" | "clicks">("impressions");
  return (
    <section aria-label="Delivery trend" className="min-w-0 rounded-lg border bg-card p-5">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Delivery trend</h3>
          <p className="mt-1 text-xs text-muted-foreground">Daily measurements in UTC</p>
        </div>
        <div className="flex rounded-md border p-0.5" role="group" aria-label="Trend metric">
          {(["impressions", "clicks"] as const).map((value) => (
            <Button
              key={value}
              size="sm"
              variant={metric === value ? "secondary" : "ghost"}
              aria-pressed={metric === value}
              onClick={() => setMetric(value)}
            >
              {value === "impressions" ? "Impressions" : "Clicks"}
            </Button>
          ))}
        </div>
      </header>
      <TrendChart
        rows={rows}
        metric={metric}
        label={metric === "clicks" ? "Clicks" : "Impressions"}
      />
    </section>
  );
}

function FormatPerformance({ data }: { data: WorkspaceDashboard }) {
  const measured = (data.format_totals ?? []).filter((row) => row.measured_days > 0);
  const max = Math.max(1, ...measured.map((row) => row.impressions));
  return (
    <section aria-label="Format comparison" className="rounded-lg border bg-card p-5">
      <h3 className="text-sm font-semibold">Performance by format</h3>
      <p className="mt-1 text-xs text-muted-foreground">Measured impressions, clicks and CTR</p>
      {measured.length ? (
        <ul className="mt-5 grid gap-5 sm:grid-cols-2">
          {measured.map((row) => (
            <li key={row.ad_format} className="min-w-0 space-y-2">
              <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-sm">
                <span className="font-medium">{adFormatLabel(row.ad_format)}</span>
                <span className="tabular-nums">{metricCount(row.impressions)} impressions</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                <div
                  className="h-full rounded-full bg-[var(--chart-1)]"
                  style={{ width: `${(100 * row.impressions) / max}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground tabular-nums">
                {metricCount(row.clicks)} clicks ·{" "}
                {row.ctr === null ? "CTR unavailable" : `${metricPercent(row.ctr)} CTR`}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-5 text-sm text-muted-foreground">
          Format comparisons appear after your first delivery measurements.
        </p>
      )}
    </section>
  );
}

export function PartnerPerformance({
  data,
  compact = false,
  accountId,
}: {
  data: WorkspaceDashboard;
  compact?: boolean;
  accountId?: string;
}) {
  const rows = performanceDays(data);
  const performanceQuery = new URLSearchParams({ days: String(rows.length) });
  if (accountId) performanceQuery.set("account_id", accountId);
  return (
    <section aria-label="Ad performance" className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Ad performance</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {data.start} to {data.end} · UTC
          </p>
        </div>
        {compact ? (
          <Button variant="outline" size="sm" asChild>
            <Link href={`${portalPath("performance")}?${performanceQuery}`}>View performance</Link>
          </Button>
        ) : (
          <Button variant="outline" size="sm" asChild>
            <a
              href={`data:text/csv;charset=utf-8,${encodeURIComponent(performanceCsv(rows))}`}
              download={`devfeed-performance-${data.start}-${data.end}.csv`}
            >
              <Download size={14} aria-hidden="true" /> Export daily CSV
            </a>
          </Button>
        )}
      </header>
      <Metrics data={data} />
      <div className={compact ? "min-w-0" : "grid min-w-0 gap-4 xl:grid-cols-2"}>
        <DeliveryTrend rows={rows} />
        {!compact && (
          <section
            aria-label="Click-through trend"
            className="min-w-0 rounded-lg border bg-card p-5"
          >
            <header className="mb-5">
              <h3 className="text-sm font-semibold">Click-through rate</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Daily clicks divided by impressions
              </p>
            </header>
            <TrendChart rows={rows} metric="ctr" label="Click-through rate" />
          </section>
        )}
      </div>
      {!compact && <FormatPerformance data={data} />}
      <p className="text-xs text-muted-foreground">
        {data.totals.measured_days} of {rows.length} days have measurements. Missing days are gaps;
        today may be incomplete. CTR uses total clicks divided by total impressions.
        {data.last_updated_at && (
          <>
            {" "}
            Last updated{" "}
            {new Date(data.last_updated_at).toLocaleString("en", {
              timeZone: "UTC",
            })}{" "}
            UTC.
          </>
        )}
      </p>
    </section>
  );
}
