"use client";

import type { ReactNode } from "react";
import { Select } from "@devfeed/ui/select";
import { ChartContainer, ChartTooltip, DistributionTooltip } from "@devfeed/ui/chart";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  Pie,
  PieChart,
  Cell,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { InfoTooltip } from "./info-tooltip";
import { formatCompactCount } from "@/lib/format-count";
import { useChartPlaceholder } from "./overview-chart-placeholder";

export const chartColors = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "#9c755f",
  "#b24d8c",
];
export function ChartCard({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  const placeholder = useChartPlaceholder();
  return (
    <section
      data-overview-card={id}
      aria-label={title}
      className="min-w-0 space-y-3 rounded-lg border bg-card p-4"
    >
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{title}</h3>
        <InfoTooltip label={title}>{description}</InfoTooltip>
      </header>
      {placeholder ?? children}
    </section>
  );
}
export function MetricStat({
  value,
  label,
  align = "start",
}: {
  value: ReactNode;
  label: string;
  align?: "start" | "end";
}) {
  return (
    <div className={`flex flex-col gap-0.5 ${align === "end" ? "items-end" : "items-start"}`}>
      <strong className="text-3xl font-semibold tabular-nums">{value}</strong>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

export function EmptyChart() {
  return (
    <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">
      No activity
    </p>
  );
}
export function ChartChoice({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <Select
      label={label}
      value={value}
      onChange={onChange}
      options={options.map(([value, label]) => ({ value, label }))}
      required
      className="w-fit min-w-40 max-w-full"
    />
  );
}
function ChartLegend({ series }: { series: [string, string][] }) {
  return (
    <ul className="mb-1 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
      {series.map(([key, name], index) => (
        <li key={key} className="inline-flex items-center gap-2">
          <span
            aria-hidden
            className="size-2.5 shrink-0 rounded-sm"
            style={{ background: chartColors[index % chartColors.length] }}
          />
          {name}
        </li>
      ))}
    </ul>
  );
}

export function TimeSeriesChart({
  rows,
  series,
  label,
  stacked = false,
  compact = false,
  unit = "",
  kind = "line",
  granularity,
}: {
  rows: object[];
  series: [string, string][];
  label: string;
  stacked?: boolean;
  compact?: boolean;
  unit?: string;
  kind?: "line" | "bar";
  granularity?: "hour" | "day" | "week" | "month" | null;
}) {
  if (!rows.length) return <EmptyChart />;
  const buckets = rows.flatMap((row) =>
    "bucket" in row && typeof row.bucket === "string" ? [row.bucket] : [],
  );
  const hourly =
    granularity === "hour" ||
    (granularity == null &&
      buckets.length > 1 &&
      buckets.some(
        (bucket, index) => index > 0 && bucket.slice(0, 10) === buckets[index - 1].slice(0, 10),
      ));
  const step = Math.max(1, Math.ceil(buckets.length / 6));
  const ticks = buckets.filter((_, index) => index % step === 0 || index === buckets.length - 1);
  const formatValue = (value: number) => `${formatCompactCount(value)}${unit ? ` ${unit}` : ""}`;
  const axis = (
    <>
      <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
      <XAxis
        dataKey="bucket"
        tickFormatter={(value: string) =>
          new Date(value).toLocaleString(
            "en",
            hourly
              ? { month: "short", day: "numeric", hour: "2-digit", hour12: false, timeZone: "UTC" }
              : { month: "short", day: "numeric", timeZone: "UTC" },
          )
        }
        ticks={ticks}
        minTickGap={20}
        tickLine={false}
        axisLine={false}
        tickMargin={10}
      />
      <YAxis
        width={45}
        tickLine={false}
        axisLine={false}
        allowDecimals={Boolean(unit)}
        tickFormatter={formatValue}
      />
      <Tooltip
        isAnimationActive={false}
        content={(props) => (
          <ChartTooltip
            {...props}
            formatLabel={(value) =>
              `${granularity === "week" ? "Week of " : granularity === "month" ? "Month of " : ""}${value.replace("T", " ").replace("Z", "")} UTC`
            }
            formatValue={formatValue}
          />
        )}
      />
    </>
  );
  const chartProps = {
    data: rows,
    accessibilityLayer: true,
    margin: { top: 10, right: 15, bottom: 5, left: 0 },
  };
  return (
    <div className="min-w-0">
      <ChartLegend series={series} />
      <ChartContainer label={label} height={compact ? 160 : 192} className="overflow-hidden">
        {kind === "bar" || stacked ? (
          <BarChart {...chartProps} barGap={2}>
            {axis}
            {series.map(([key, name], index) => (
              <Bar
                key={key}
                dataKey={key}
                name={name}
                fill={chartColors[index]}
                stackId={stacked ? "usage" : undefined}
                maxBarSize={42}
                radius={[2, 2, 0, 0]}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        ) : (
          <LineChart {...chartProps}>
            {axis}
            {series.map(([key, name], index) => (
              <Line
                key={key}
                type="linear"
                dataKey={key}
                name={name}
                stroke={chartColors[index]}
                strokeWidth={2}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        )}
      </ChartContainer>
    </div>
  );
}

export function BreakdownChart({
  rows,
  label,
  series,
}: {
  rows: object[];
  label: string;
  series: [string, string][];
}) {
  if (!rows.length) return <EmptyChart />;
  return (
    <div className="min-w-0">
      <ChartLegend series={series} />
      <ChartContainer label={label} height={224} className="overflow-hidden">
        <BarChart
          data={rows}
          layout="vertical"
          accessibilityLayer
          margin={{ top: 10, left: 0, right: 15, bottom: 5 }}
          barGap={2}
        >
          <CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="3 4" />
          <XAxis
            type="number"
            tickFormatter={formatCompactCount}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={136}
            tick={{ width: 128 }}
            tickLine={false}
            axisLine={false}
            interval={0}
          />
          <Tooltip
            isAnimationActive={false}
            content={(props) => <ChartTooltip {...props} formatValue={formatCompactCount} />}
          />
          {series.map(([key, name], index) => (
            <Bar
              key={key}
              dataKey={key}
              name={name}
              fill={chartColors[index]}
              maxBarSize={42}
              radius={[0, 2, 2, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ChartContainer>
    </div>
  );
}

/** A compact metric trend shares the same axes, units, and tooltip behavior. */
export function MetricSparkline(props: Omit<Parameters<typeof TimeSeriesChart>[0], "compact">) {
  return <TimeSeriesChart {...props} compact />;
}

export function DonutChart({
  rows,
  label,
  children,
  legend = false,
}: {
  rows: { name: string; value: number }[];
  label: string;
  children?: ReactNode;
  legend?: boolean;
}) {
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  return (
    <div className="min-w-0">
      <div className="relative">
        <ChartContainer label={label} height={176} className="overflow-hidden">
          <PieChart accessibilityLayer>
            <Pie
              data={rows}
              dataKey="value"
              nameKey="name"
              innerRadius={52}
              outerRadius={72}
              startAngle={90}
              endAngle={-270}
              paddingAngle={2}
              stroke="var(--card)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {rows.map((row, index) => (
                <Cell key={row.name} fill={chartColors[index % chartColors.length]} />
              ))}
            </Pie>
            <Tooltip
              isAnimationActive={false}
              content={(props) => <DistributionTooltip {...props} total={total} />}
            />
          </PieChart>
        </ChartContainer>
        {children && (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            {children}
          </div>
        )}
      </div>
      {legend && (
        <ul className="flex flex-wrap justify-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          {rows.map((row, index) => (
            <li key={row.name} className="inline-flex items-center gap-1.5">
              <span
                className="size-2.5 rounded-sm"
                style={{ background: chartColors[index % chartColors.length] }}
              />
              {row.name}{" "}
              <span className="tabular-nums text-foreground">{row.value.toLocaleString("en")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
