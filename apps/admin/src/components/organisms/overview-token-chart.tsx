"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import { formatCompactCount } from "@/lib/format-count";
import { InfoTooltip } from "@/components/molecules/info-tooltip";
import type { AutomationOverview } from "@/lib/api/generated/models";

const series = [
  { key: "article_analysis", label: "Article analysis", color: "var(--chart-1)" },
  { key: "topic_analysis", label: "Topic analysis", color: "var(--chart-2)" },
  { key: "research_verification", label: "Research verification", color: "var(--chart-3)" },
] as const;
const number = (value: number) => value.toLocaleString("en");
const shortDate = (value: string) => new Date(`${value}T00:00:00Z`).toLocaleDateString("en", { month: "short", day: "numeric", timeZone: "UTC" });

export function OverviewTokenChart({ data }: { data: AutomationOverview | null | undefined }) {
  const rows = data?.token_activity ?? [];
  const totals = series.map(item => ({ ...item, total: rows.reduce((sum, day) => sum + (day[item.key] ?? 0), 0) }));
  const reported = rows.reduce((sum, day) => sum + (day.reported_runs ?? 0), 0);
  const missing = rows.reduce((sum, day) => sum + (day.unreported_runs ?? 0), 0);
  return <section aria-label="AI token usage" className="min-w-0 border-t pt-5">
    <div className="mb-4 flex items-center gap-2"><h3 className="text-sm font-semibold">Daily AI tokens by job type</h3><InfoTooltip label="Daily AI tokens by job type">Reported tokens for finished jobs, including failed jobs and recorded retries, grouped by the job’s completion day in UTC. Today is in progress. Topic analysis includes relationship research. {number(reported)} jobs reported usage; {number(missing)} finished jobs have no usable token total. Running jobs and source relevance checks are excluded because their usage is not available here. These are reported tokens, not billing figures.</InfoTooltip></div>
    <ul aria-label="Token totals by job type" className="mb-5 flex flex-wrap gap-x-8 gap-y-3">
      {totals.map(item => <li key={item.key} className="flex items-center gap-2 text-sm"><span aria-hidden className="size-2.5 rounded-sm" style={{ background: item.color }} /><span className="text-muted-foreground">{item.label}</span><strong className="ml-1 font-medium tabular-nums">{formatCompactCount(item.total)}</strong><InfoTooltip label={`${item.label} token total`}>{number(item.total)} reported tokens in this period.</InfoTooltip></li>)}
    </ul>
    {totals.some(item => item.total > 0) ? <ChartContainer label="Daily reported AI tokens by job type" className="h-72">
      <BarChart data={rows} accessibilityLayer margin={{ top: 10, right: 12, bottom: 5, left: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
        <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} minTickGap={40} tickMargin={10} />
        <YAxis tickFormatter={formatCompactCount} tickLine={false} axisLine={false} allowDecimals={false} width={60} />
        <Tooltip content={props => {
          const day = props.payload?.[0]?.payload;
          if (!props.active || !day) return null;
          const total = series.reduce((sum, item) => sum + (day[item.key] ?? 0), 0);
          return <div><ChartTooltip {...props} formatLabel={shortDate} formatValue={formatCompactCount} /><div className="rounded-b-lg border border-t-0 bg-popover px-3 py-2 text-xs text-popover-foreground"><p className="font-medium">Total: {formatCompactCount(total)} tokens</p><p className="mt-1 text-muted-foreground">{formatCompactCount(day.reported_runs ?? 0)} jobs with usage{day.unreported_runs ? ` · ${formatCompactCount(day.unreported_runs)} without usage` : ""}</p></div></div>;
        }} />
        {series.map(item => <Bar key={item.key} dataKey={item.key} name={item.label} fill={item.color} stackId="tokens" maxBarSize={48} isAnimationActive={false} />)}
      </BarChart>
    </ChartContainer> : <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">{missing ? "Token usage is unavailable for finished jobs in this period." : "No reported AI token usage in this period."}</p>}
  </section>;
}
