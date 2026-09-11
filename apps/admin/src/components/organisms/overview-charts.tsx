"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { InfoTooltip } from "@/components/molecules/info-tooltip";
import { Button } from "@/components/atoms/button";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import type { AdminOverview } from "@/lib/api/generated/models";
import { humanize } from "@/lib/resources";

const shortDate = (value: string) => new Date(`${value}T00:00:00Z`).toLocaleDateString("en", { month: "short", day: "numeric", timeZone: "UTC" });
const colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];
const typeColors: Record<string, string> = Object.fromEntries(["article", "news", "tutorial", "release", "comparison", "opinion"].map((type, index) => [type, colors[index]]));
const typeColor = (type: string) => typeColors[type] ?? "var(--muted-foreground)";
function Legend({ items }: { items: [string, string][] }) {
  return <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">{items.map(([label, color]) => <span key={label} className="inline-flex items-center gap-2"><span aria-hidden className="size-2.5 rounded-sm" style={{ background: color }} />{label}</span>)}</div>;
}

export function OverviewCharts({ data }: { data: AdminOverview }) {
  const [breakdown, setBreakdown] = useState(false);
  const activity = data.insights?.reader_activity ?? [];
  const types = [...new Set(activity.flatMap(day => Object.keys(day.content_types ?? {})))].sort();
  const rows = activity.map(day => ({ ...day, ...day.content_types }));
  const axes = <><CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" /><XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} minTickGap={40} tickMargin={10} /><YAxis tickLine={false} axisLine={false} allowDecimals={false} width={45} /></>;
  return <section aria-label="Platform activity" className="min-w-0 overflow-hidden rounded-lg border bg-card"><div className="flex items-center gap-2 border-b px-5 py-4"><h2 className="text-base font-semibold">Activity</h2><InfoTooltip label="Activity">Daily totals for the selected {data.days} days, in UTC. Today is in progress.</InfoTooltip></div><div className="grid min-w-0 divide-y xl:grid-cols-2 xl:divide-x xl:divide-y-0">
    <section className="min-w-0 p-5"><div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><h3 className="text-sm font-medium">Publishing activity</h3><InfoTooltip label="Publishing activity">{breakdown ? "First publications by content type." : "Discovered articles and first publications each day."}</InfoTooltip></div><div className="flex rounded-md border p-0.5" role="group" aria-label="Publication chart display"><Button size="sm" variant={!breakdown ? "secondary" : "ghost"} aria-pressed={!breakdown} onClick={() => setBreakdown(false)}>Totals</Button><Button size="sm" variant={breakdown ? "secondary" : "ghost"} aria-pressed={breakdown} onClick={() => setBreakdown(true)}>Content types</Button></div></div>
      <Legend items={breakdown ? types.map(type => [humanize(type), typeColor(type)]) : [["Discovered", colors[0]], ["First published", colors[1]]]} />
    </div><div className="mt-4 -ml-3">{activity.some(day => day.added || day.published) ? <ChartContainer label="Daily articles discovered and first published" className="h-64"><BarChart data={rows} accessibilityLayer barGap={2} margin={{ top: 10, right: 15, bottom: 5, left: 0 }}>{axes}<Tooltip content={props => <ChartTooltip {...props} formatLabel={shortDate} />} />{breakdown ? types.map(type => <Bar key={type} dataKey={type} name={humanize(type)} fill={typeColor(type)} stackId="types" isAnimationActive={false} />) : <><Bar dataKey="added" name="Discovered" fill={colors[0]} radius={[2, 2, 0, 0]} isAnimationActive={false} /><Bar dataKey="published" name="First published" fill={colors[1]} radius={[2, 2, 0, 0]} isAnimationActive={false} /></>}</BarChart></ChartContainer> : <p className="flex h-64 items-center justify-center text-sm text-muted-foreground">No publishing activity in this period.</p>}</div></section>
    <section className="min-w-0 p-5"><div className="space-y-4"><div className="flex min-h-10 items-center gap-2"><h3 className="text-sm font-medium">Reader activity</h3><InfoTooltip label="Reader activity">Clicks to the original article, counted once per viewer, article, and hour. {activity.some(day => day.opens == null) && "Some open history is unavailable. Missing days are gaps, not zero activity."}</InfoTooltip></div><Legend items={[["Original article clicks", colors[0]]]} /></div><div className="mt-4 -ml-3">{activity.some(day => day.opens) ? <ChartContainer label="Daily original article clicks" className="h-64"><LineChart data={activity} accessibilityLayer margin={{ top: 10, right: 15, bottom: 5, left: 0 }}>{axes}<Tooltip content={props => <ChartTooltip {...props} formatLabel={shortDate} />} /><Line type="linear" dataKey="opens" name="Original article clicks" stroke={colors[0]} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} /></LineChart></ChartContainer> : <p className="flex h-64 items-center justify-center text-sm text-muted-foreground">No recorded original article clicks in the available history.</p>}{activity.some(day => day.opens == null) && <span className="sr-only">Some open history is unavailable.</span>}</div></section>
  </div></section>;
}
