"use client";

import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";

export type DistributionRow = { label: string; value: number; color: string };
export function DistributionChart({ label, rows, centerLabel = "Total" }: { label: string; rows: DistributionRow[]; centerLabel?: string }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  return <div className="flex flex-wrap items-center justify-center gap-8">
    <div className="relative w-56 shrink-0">
      {total ? <ChartContainer label={label} className="h-56"><PieChart accessibilityLayer><Pie data={rows.filter(row => row.value > 0)} dataKey="value" nameKey="label" innerRadius="72%" outerRadius="95%" stroke="var(--card)" strokeWidth={3} isAnimationActive={false}>{rows.filter(row => row.value > 0).map(row => <Cell key={row.label} fill={row.color} />)}</Pie><Tooltip content={({ active, payload }) => active && payload?.length ? <div className="rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground">{payload[0].name}: {Number(payload[0].value).toLocaleString("en")} ({(100 * Number(payload[0].value) / total).toFixed(1)}%)</div> : null} /></PieChart></ChartContainer> : <div className="h-56" />}
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"><strong className="text-3xl font-semibold tabular-nums">{total.toLocaleString("en")}</strong><span className="mt-1 text-xs text-muted-foreground">{centerLabel}</span></div>
    </div>
    <ul className="min-w-44 space-y-4 text-sm">{rows.map(row => <li key={row.label} className="flex items-center gap-2"><span aria-hidden className="size-2.5 rounded-sm" style={{ background: row.color }} /><span className="text-muted-foreground">{row.label}</span><strong className="ml-auto pl-6 font-medium tabular-nums">{row.value.toLocaleString("en")}</strong></li>)}</ul>
  </div>;
}

export type CoveragePoint = { name: string; x: number; y: number; size: number; needsAttention: boolean };
export function CoverageChart({ label, rows, xLabel, yLabel, sizeLabel }: { label: string; rows: CoveragePoint[]; xLabel: string; yLabel: string; sizeLabel: string }) {
  if (!rows.length) return <p className="py-16 text-center text-sm text-muted-foreground">No data available for this comparison.</p>;
  if (rows.length === 1) return <div className="py-6"><p className="mb-5 text-sm font-medium">{rows[0].name}</p><div className="grid grid-cols-3 gap-4">{[{ label: xLabel, value: rows[0].x }, { label: yLabel, value: rows[0].y }, { label: sizeLabel, value: rows[0].size }].map(item => <div key={item.label}><p className="text-xs text-muted-foreground">{item.label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{item.value.toLocaleString("en")}</p></div>)}</div></div>;
  return <div><div className="mb-3 flex gap-5 text-xs text-muted-foreground"><span><span className="mr-2 inline-block size-2 rounded-full bg-chart-1" />{label === "Interest versus coverage" ? "Has recent content" : "Fetching normally"}</span><span><span className="mr-2 inline-block size-2 rounded-full bg-chart-3" />{label === "Interest versus coverage" ? "No new content" : "Fetch failures"}</span></div>
    <ChartContainer label={label} className="h-80"><ScatterChart accessibilityLayer margin={{ top: 15, right: 25, bottom: 30, left: 15 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 4" /><XAxis type="number" dataKey="x" name={xLabel} domain={[0, Math.max(1, ...rows.map(row => row.x))]} allowDecimals={false} tickLine={false} axisLine={false} label={{ value: xLabel, position: "bottom", offset: 10 }} /><YAxis type="number" dataKey="y" name={yLabel} domain={[0, Math.max(1, ...rows.map(row => row.y))]} allowDecimals={false} tickLine={false} axisLine={false} width={35} /><ZAxis dataKey="size" range={[70, 300]} name={sizeLabel} /><Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }) => {
      if (!active || !payload?.length) return null;
      const point = payload[0].payload as CoveragePoint;
      return <div className="max-w-64 rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md"><p className="mb-2 font-medium">{point.name}</p><p>{xLabel}: {point.x}</p><p>{yLabel}: {point.y}</p><p>{sizeLabel}: {point.size}</p></div>;
    }} /><Scatter data={rows} isAnimationActive={false}>{rows.map((row, index) => <Cell key={index} fill={row.needsAttention ? "var(--chart-3)" : "var(--chart-1)"} stroke="var(--card)" strokeWidth={1} />)}</Scatter></ScatterChart></ChartContainer>
    <p className="sr-only">{yLabel} on the vertical axis. {rows.map(row => `${row.name}: ${row.x} ${xLabel}, ${row.y} ${yLabel}, ${row.size} ${sizeLabel}.`).join(" ")}</p>
  </div>;
}

export function JobOutcomesChart({ rows }: { rows: { name: string; completed: number; failed: number }[] }) {
  const active = rows.filter(row => row.completed + row.failed > 0);
  if (!active.length) return <p className="py-16 text-center text-sm text-muted-foreground">No completed or failed jobs in this period.</p>;
  return <><div className="mb-3 flex gap-5 text-xs text-muted-foreground"><span><span className="mr-2 inline-block size-2 rounded-sm bg-chart-2" />Completed</span><span><span className="mr-2 inline-block size-2 rounded-sm bg-destructive" />Failed</span></div><ChartContainer label="Job outcome rates" className="h-80"><BarChart data={active} layout="vertical" stackOffset="expand" accessibilityLayer margin={{ right: 15, top: 5, bottom: 5 }}><CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="3 4" /><XAxis type="number" domain={[0, 1]} tickFormatter={value => `${Math.round(value * 100)}%`} tickLine={false} axisLine={false} /><YAxis type="category" dataKey="name" width={120} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} /><Tooltip content={props => <ChartTooltip {...props} />} /><Bar dataKey="completed" name="Completed" stackId="outcomes" fill="var(--chart-2)" maxBarSize={24} isAnimationActive={false} /><Bar dataKey="failed" name="Failed" stackId="outcomes" fill="var(--destructive)" maxBarSize={24} isAnimationActive={false} /></BarChart></ChartContainer></>;
}
