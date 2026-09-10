"use client";

import { useId } from "react";
import Link from "next/link";
import { Area, AreaChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/atoms/card";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import type { AdminOverview } from "@/lib/api/generated/models";

const shortDate = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString("en", { month: "short", day: "numeric", timeZone: "UTC" });
const longDate = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString("en", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

function Legend({ color, value, label }: { color: string; value: number; label: string }) {
  return <span className="flex items-center gap-2 text-xs"><span aria-hidden className="size-2 rounded-full" style={{ background: color }} /><strong className="font-semibold tabular-nums">{value.toLocaleString("en")}</strong><span className="text-muted-foreground">{label}</span></span>;
}

export function OverviewCharts({ data }: { data: AdminOverview }) {
  const gradient = useId().replace(/:/g, "");
  const added = data.activity.reduce((total, day) => total + day.added, 0);
  const published = data.activity.reduce((total, day) => total + day.published, 0);
  const analysis = data.analysis_activity ?? [];
  const axis = <><CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" /><XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={10} minTickGap={40} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} width={42} /></>;
  return <div className="space-y-3">
    <div className="grid min-w-0 gap-4 xl:grid-cols-2">
      <Card className="min-w-0 gap-4 py-5 shadow-none"><CardHeader className="px-5"><CardTitle><h2>Content activity</h2></CardTitle><div className="mt-1 flex flex-wrap gap-4"><Legend color="var(--chart-1)" value={added} label="added" /><Legend color="var(--chart-2)" value={published} label="first published" /></div></CardHeader><CardContent className="px-3">
        {data.activity.length ? <ChartContainer label="Daily articles added and first published" className="h-52 sm:h-56"><AreaChart data={data.activity} accessibilityLayer aria-label="Content activity. Use arrow keys to explore daily counts." margin={{ top: 10, right: 15, bottom: 0, left: -10 }}>
          <defs><linearGradient id={`${gradient}-content`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.18} /><stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.01} /></linearGradient></defs>
          {axis}<Tooltip content={props => <ChartTooltip {...props} formatLabel={longDate} />} />
          <Area dataKey="added" name="Added" type="linear" stroke="var(--chart-1)" strokeWidth={2} fill={`url(#${gradient}-content)`} isAnimationActive={false} />
          <Area dataKey="published" name="First published" type="linear" stroke="var(--chart-2)" strokeWidth={2} strokeDasharray="5 3" fill="transparent" isAnimationActive={false} />
        </AreaChart></ChartContainer> : <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">No content activity yet</p>}
      </CardContent></Card>
      <Card className="min-w-0 gap-4 py-5 shadow-none"><CardHeader className="px-5"><CardTitle><h2>AI processing</h2></CardTitle><div className="mt-1 flex flex-wrap gap-4"><Legend color="var(--chart-2)" value={data.analysis.succeeded} label="succeeded" /><Legend color="var(--destructive)" value={data.analysis.failed} label="failed" /></div></CardHeader><CardContent className="px-3">
        {analysis.length ? <ChartContainer label="Daily AI analysis outcomes" className="h-52 sm:h-56"><AreaChart data={analysis} accessibilityLayer aria-label="AI processing. Use arrow keys to explore daily completed runs." margin={{ top: 10, right: 15, bottom: 0, left: -10 }}>
          <defs><linearGradient id={`${gradient}-ai`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.18} /><stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0.01} /></linearGradient></defs>
          {axis}<Tooltip content={props => <ChartTooltip {...props} formatLabel={longDate} />} />
          <Area dataKey="succeeded" name="Succeeded" type="linear" stroke="var(--chart-2)" strokeWidth={2} fill={`url(#${gradient}-ai)`} isAnimationActive={false} />
          <Area dataKey="failed" name="Failed" type="linear" stroke="var(--destructive)" strokeWidth={2} strokeDasharray="5 3" fill="transparent" isAnimationActive={false} />
        </AreaChart></ChartContainer> : <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">No AI activity history available</p>}
      </CardContent></Card>
    </div>
    <p className="text-xs text-muted-foreground">Last {data.days} days · Daily totals in UTC · Today is in progress</p>
  </div>;
}

export function TopicCoverage({ data }: { data: AdminOverview }) {
  return <details className="rounded-lg border bg-card"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">Top topics in published articles</summary><div className="border-t px-5 py-2">{data.top_topics.length ? <ol className="divide-y">{data.top_topics.map(topic => <li key={topic.id} className="flex items-center justify-between gap-4 py-3 text-sm"><Link href={`/taxonomy/topics/${topic.id}`} className="font-medium hover:underline">{topic.name}</Link><span className="text-muted-foreground">{topic.articles.toLocaleString("en")} articles</span></li>)}</ol> : <p className="py-3 text-sm text-muted-foreground">No published topic coverage yet.</p>}</div></details>;
}
