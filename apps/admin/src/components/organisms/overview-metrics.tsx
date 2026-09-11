"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip } from "recharts";
import { InfoTooltip } from "@/components/molecules/info-tooltip";
import type { AdminOverview, OverviewMetric } from "@/lib/api/generated/models";

export function duration(seconds: number | null | undefined) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} hr`;
  return `${(seconds / 86400).toFixed(1)} days`;
}
const count = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("en", { maximumFractionDigits: 0 });

function comparison(metric: OverviewMetric, isDuration = false) {
  if (metric.current == null) return "No complete measurement for this period";
  if (metric.previous == null) return "Previous period unavailable";
  const difference = metric.current - metric.previous;
  if (!difference) return "No change";
  if (isDuration) return `${duration(Math.abs(difference))} ${difference > 0 ? "slower" : "faster"} than previous period`;
  if (!metric.previous) return `+${count(difference)} vs previous period`;
  return `${difference > 0 ? "+" : ""}${(100 * difference / metric.previous).toFixed(1)}% vs previous period`;
}

function Sparkline({ values, dates, label, isDuration }: { values: (number | null | undefined)[]; dates: string[]; label: string; isDuration?: boolean }) {
  if (!values.length) return null;
  const rows = values.map((value, index) => ({ date: dates[index], value: value ?? null }));
  return <div className="h-10 w-28 shrink-0" role="group" aria-label={`${label} daily trend`}>
    <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 112, height: 40 }}>
      <LineChart data={rows} accessibilityLayer margin={{ top: 4, bottom: 4, left: 4, right: 4 }}>
        <Tooltip filterNull={false} isAnimationActive={false} position={{ x: -120, y: 44 }} cursor={false} content={({ active, payload }) => {
          if (!active || !payload?.length) return null;
          const day = payload[0].payload as { date: string; value: number | null };
          return <div className="relative z-50 w-56 rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md"><p className="mb-1 text-muted-foreground">{new Date(`${day.date}T00:00:00Z`).toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })} · UTC</p><p className="flex items-center justify-between gap-3"><span>{label}</span><strong className="tabular-nums">{day.value == null ? "No data" : isDuration ? duration(day.value) : count(day.value)}</strong></p></div>;
        }} />
        <Line dataKey="value" name={label} type="linear" stroke="var(--chart-1)" strokeWidth={2} dot={false} activeDot={{ r: 3, fill: "var(--chart-1)", stroke: "var(--card)" }} connectNulls={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  </div>;
}

export function OverviewMetrics({ data }: { data: AdminOverview }) {
  const insight = data.insights!;
  const days = insight.reader_activity ?? [];
  const metrics = [
    { label: "First publications", metric: insight.publications!, values: days.map(day => day.published), info: "Articles first published to the feed during this period." },
    { label: "Original article clicks", metric: insight.opens!, values: days.map(day => day.opens), info: "Clicks to the original article, counted once per viewer, article, and hour. Unavailable history is not counted as zero." },
    { label: "New accounts", metric: insight.accounts!, values: days.map(day => day.accounts), info: "Accounts created during this period." },
    { label: "Median publication time", metric: insight.publication_seconds!, values: days.map(day => day.median_publication_seconds), info: `Time from discovery to first publication. 90th percentile: ${duration(insight.publication_p90_seconds)}.`, duration: true },
  ];
  return <div>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(item => <div key={item.label} className="min-w-0 rounded-lg border bg-card p-5">
      <div className="flex items-center justify-between gap-2"><p className="text-sm text-muted-foreground">{item.label}</p><InfoTooltip label={item.label}>{item.info} Comparisons use the previous {data.days} calendar days; today is in progress. Daily totals use UTC.</InfoTooltip></div>
      <div className="my-3 flex flex-wrap items-center justify-between gap-2"><strong className="text-3xl font-semibold tracking-tight tabular-nums">{item.duration ? duration(item.metric.current) : count(item.metric.current)}</strong><Sparkline values={item.values} dates={days.map(day => day.date)} label={item.label} isDuration={item.duration} /></div>
      <p className="text-xs text-muted-foreground">{comparison(item.metric, item.duration)}</p>
    </div>)}</div>
  </div>;
}
