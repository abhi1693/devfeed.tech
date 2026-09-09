"use client";

import { useId } from "react";
import Link from "next/link";
import { ArrowUpRight, ChartNoAxesCombined, Tags } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import type { AdminOverview } from "@/lib/api/generated/models";
import { resourceHref } from "@/lib/routes";

const colors = { added: "var(--chart-1)", published: "var(--chart-2)" };
const shortDate = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString("en", { month: "short", day: "numeric", timeZone: "UTC" });
const longDate = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString("en", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

export function OverviewCharts({ data }: { data: AdminOverview }) {
  const gradient = useId().replace(/:/g, "");
  const added = data.activity.reduce((total, day) => total + day.added, 0);
  const published = data.activity.reduce((total, day) => total + day.published, 0);

  return <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]">
    <Card className="min-w-0 gap-5 shadow-none">
      <CardHeader className="gap-2 px-5 sm:px-6">
        <CardTitle><h2>Content activity</h2></CardTitle>
        <CardDescription>Articles added and first published over the last {data.days} days.</CardDescription>
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <span className="flex items-center gap-2"><span aria-hidden className="size-2 rounded-full bg-chart-1" /><strong className="font-semibold tabular-nums">{added.toLocaleString("en")}</strong><span className="text-muted-foreground">added</span></span>
          <span className="flex items-center gap-2"><span aria-hidden className="h-0.5 w-3 border-t-2 border-dashed border-chart-2" /><strong className="font-semibold tabular-nums">{published.toLocaleString("en")}</strong><span className="text-muted-foreground">first published</span></span>
        </div>
      </CardHeader>
      <CardContent className="min-w-0 px-3 sm:px-5">
        {added || published ? <ChartContainer label="Daily articles added and first published" className="h-64 sm:h-72">
          <AreaChart data={data.activity} accessibilityLayer aria-label="Content activity. Use left and right arrow keys to explore daily counts." margin={{ top: 8, right: 10, bottom: 0, left: -15 }}>
            <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={colors.added} stopOpacity={0.16} /><stop offset="100%" stopColor={colors.added} stopOpacity={0.01} /></linearGradient></defs>
            <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
            <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={12} minTickGap={36} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} tickMargin={8} width={48} />
            <Tooltip content={props => <ChartTooltip {...props} formatLabel={longDate} />} cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 4" }} />
            <Area dataKey="added" name="Added" type="linear" stroke={colors.added} strokeWidth={2} fill={`url(#${gradient})`} activeDot={{ r: 4 }} isAnimationActive={false} />
            <Area dataKey="published" name="First published" type="linear" stroke={colors.published} strokeWidth={2} strokeDasharray="5 3" fill="transparent" activeDot={{ r: 4 }} isAnimationActive={false} />
          </AreaChart>
        </ChartContainer> : <div className="flex h-64 flex-col items-center justify-center gap-3 px-5 text-center sm:h-72">
          <ChartNoAxesCombined aria-hidden className="size-8 text-muted-foreground/60" />
          <p className="text-sm font-medium">No content activity yet</p>
          <p className="max-w-xs text-xs leading-5 text-muted-foreground">New articles and first publications will appear here as your feed grows.</p>
          <Link className="text-xs underline underline-offset-4" href={resourceHref("sources")}>Manage sources</Link>
        </div>}
        <p className="mt-3 px-2 text-xs text-muted-foreground">Daily totals in UTC · Today is still in progress</p>
      </CardContent>
    </Card>
    <Card className="min-w-0 gap-5 shadow-none">
      <CardHeader className="gap-2 px-5 sm:px-6"><CardTitle><h2>Top topics</h2></CardTitle><CardDescription>Most represented in the live feed · All time</CardDescription></CardHeader>
      <CardContent className="flex min-w-0 flex-1 flex-col px-3 sm:px-5">
        {data.top_topics.length ? <ChartContainer label="Top five active topics by published article count" className="h-64 min-h-64 flex-1">
          <BarChart data={data.top_topics} layout="vertical" accessibilityLayer aria-label="Top topics. Use arrow keys to explore published article counts." margin={{ left: 0, right: 20, top: 4, bottom: 0 }} barSize={16}>
            <CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="3 4" />
            <XAxis type="number" allowDecimals={false} tickLine={false} axisLine={false} tickMargin={10} />
            <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={104} tickMargin={12} tickFormatter={name => name.length > 15 ? `${name.slice(0, 14)}…` : name} />
            <Tooltip content={props => <ChartTooltip {...props} />} cursor={{ fill: "var(--muted)", opacity: 0.6 }} />
            <Bar dataKey="articles" name="Published articles" fill={colors.added} fillOpacity={0.8} radius={[0, 4, 4, 0]} isAnimationActive={false} />
          </BarChart>
        </ChartContainer> : <div className="flex min-h-64 flex-1 flex-col items-center justify-center gap-3 px-5 text-center">
          <Tags aria-hidden className="size-8 text-muted-foreground/60" />
          <p className="text-sm font-medium">Your topic coverage starts here</p>
          <p className="max-w-xs text-xs leading-5 text-muted-foreground">Publish articles with active topics to see what your feed covers.</p>
        </div>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 px-1 text-xs">
          <span className="text-muted-foreground">An article can cover multiple topics.</span>
          <Link href={resourceHref("topics")} className="inline-flex items-center gap-1 font-medium hover:underline">View topics <ArrowUpRight aria-hidden className="size-3.5" /></Link>
        </div>
      </CardContent>
    </Card>
  </div>;
}
