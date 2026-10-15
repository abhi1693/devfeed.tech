"use client";

import { useState } from "react";
import Link from "next/link";
import { Download, Eye, MousePointerClick, TrendingUp } from "lucide-react";
import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartContainer, ChartTooltip, DistributionTooltip } from "./chart";
import { Button } from "./button";
import { adFormatLabel, adFormats } from "./advertising-formats";
import {
  metricCount,
  metricPercent,
  performanceCsv,
  performanceDays,
  shortMetricDate,
  type PerformanceDay,
  type PerformanceReport,
} from "./performance";

function Metrics({ data }: { data: PerformanceReport }) {
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
    <div className="grid grid-cols-3 divide-x rounded-lg border bg-card">
      {metrics.map(({ label, value, hint, icon: Icon }) => (
        <section key={label} aria-label={label} className="min-w-0 px-3 py-3 sm:px-4">
          <div className="flex min-h-8 items-start justify-between gap-2 text-xs text-muted-foreground sm:min-h-0 sm:items-center sm:text-sm">
            <h3 className="font-medium">{label}</h3>
            <Icon size={14} aria-hidden="true" className="hidden shrink-0 sm:block" />
          </div>
          <p className="mt-2 break-words text-[clamp(1rem,4vw,1.5rem)] font-semibold leading-tight tracking-tight tabular-nums">
            {value}
          </p>
          <p className="mt-1 hidden text-xs text-muted-foreground sm:block">{hint}</p>
        </section>
      ))}
    </div>
  );
}

const deliverySeries = [
  {
    metric: "impressions",
    label: "Impressions",
    axis: "left",
    color: "var(--chart-1)",
    dash: undefined,
  },
  {
    metric: "clicks",
    label: "Clicks",
    axis: "right",
    color: "var(--chart-2)",
    dash: "5 3",
  },
] as const;

function TrendChart({
  rows,
  metric,
  label,
  height = 192,
}: {
  rows: PerformanceDay[];
  metric: "delivery" | "ctr";
  label: string;
  height?: number;
}) {
  const percentage = metric === "ctr";
  const format = percentage ? metricPercent : metricCount;
  const series = percentage
    ? [
        {
          metric: "ctr" as const,
          label,
          axis: "left" as const,
          color: "var(--chart-2)",
          dash: undefined,
        },
      ]
    : deliverySeries;
  if (!rows.some((row) => series.some((item) => row[item.metric] !== null)))
    return (
      <p
        style={{ minHeight: height }}
        className="flex items-center justify-center px-4 text-center text-sm text-muted-foreground"
      >
        {percentage
          ? "Click-through rate appears after the first measured impression."
          : "Your delivery trend will appear when measurements arrive."}
      </p>
    );
  return (
    <ChartContainer label={label} height={height} className="overflow-hidden">
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
        {series.map((item) => (
          <YAxis
            key={item.metric}
            yAxisId={item.metric}
            orientation={item.axis}
            tickLine={false}
            axisLine={false}
            allowDecimals={percentage}
            tickFormatter={
              percentage
                ? (value: number) => `${value.toLocaleString("en", { maximumFractionDigits: 1 })}%`
                : (value: number) => value.toLocaleString("en", { notation: "compact" })
            }
            domain={[0, "auto"]}
            width={percentage ? 58 : 42}
          />
        ))}
        <Tooltip
          content={(props) => (
            <ChartTooltip {...props} formatLabel={shortMetricDate} formatValue={format} />
          )}
        />
        {series.map((item) => (
          <Line
            key={item.metric}
            yAxisId={item.metric}
            type="linear"
            dataKey={item.metric}
            name={item.label}
            stroke={item.color}
            strokeWidth={2}
            strokeDasharray={item.dash}
            dot={{ r: item.metric === "clicks" ? 3 : 2 }}
            activeDot={{ r: item.metric === "clicks" ? 5 : 4 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}

function DeliveryTrend({ rows, compact }: { rows: PerformanceDay[]; compact: boolean }) {
  return (
    <section aria-label="Delivery trend" className="min-w-0 rounded-lg border bg-card p-4">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Delivery trend</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Daily counts in UTC · separate scales
          </p>
        </div>
        <ul aria-label="Chart series" className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {deliverySeries.map((item) => (
            <li key={item.metric} className="inline-flex items-center gap-1.5">
              <svg width="20" height="8" aria-hidden="true">
                <line
                  x1="0"
                  y1="4"
                  x2="20"
                  y2="4"
                  stroke={item.color}
                  strokeWidth="2"
                  strokeDasharray={item.dash}
                />
              </svg>
              <span>
                {item.label} ({item.axis})
              </span>
            </li>
          ))}
        </ul>
      </header>
      <TrendChart
        rows={rows}
        metric="delivery"
        label="Impressions and clicks"
        height={compact ? 176 : 192}
      />
    </section>
  );
}

const formatColors = new Map<string, string>(
  adFormats.map((format, index) => [format.value, `var(--chart-${index + 1})`]),
);

function FormatPerformance({
  data,
  compact = false,
}: {
  data: PerformanceReport;
  compact?: boolean;
}) {
  const [metric, setMetric] = useState<"impressions" | "clicks">("impressions");
  const measured = (data.format_totals ?? []).filter((row) => row.measured_days > 0);
  const formats = measured.map((row) => ({
    ...row,
    name: adFormatLabel(row.ad_format),
    value: row[metric],
    color: formatColors.get(row.ad_format) ?? "var(--chart-5)",
  }));
  const total = formats.reduce((sum, row) => sum + row.value, 0);
  const slices = formats.filter((row) => row.value > 0);
  const label = metric === "impressions" ? "Impression share by format" : "Click share by format";
  return (
    <section aria-label="Format comparison" className="min-w-0 rounded-lg border bg-card p-4">
      <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Performance by format</h3>
          <p className="mt-1 text-xs text-muted-foreground">Share of {metric}</p>
        </div>
        <div
          role="group"
          aria-label="Format metric"
          className="inline-flex shrink-0 gap-0.5 rounded-md bg-muted p-0.5"
        >
          {(["impressions", "clicks"] as const).map((value) => (
            <Button
              key={value}
              size="xs"
              variant="ghost"
              aria-pressed={metric === value}
              className={metric === value ? "bg-card shadow-xs" : "text-muted-foreground"}
              onClick={() => setMetric(value)}
            >
              {value === "impressions" ? "Impressions" : "Clicks"}
            </Button>
          ))}
        </div>
      </header>
      {measured.length ? (
        <div
          style={{ minHeight: compact ? 176 : 192 }}
          className="flex flex-wrap items-center justify-center gap-x-3 gap-y-4"
        >
          <div className="relative w-32 shrink-0">
            {total > 0 ? (
              <ChartContainer label={label} height={144}>
                <PieChart accessibilityLayer>
                  <Pie
                    data={slices}
                    dataKey="value"
                    nameKey="name"
                    innerRadius="72%"
                    outerRadius="100%"
                    stroke="var(--card)"
                    strokeWidth={3}
                    isAnimationActive={false}
                  >
                    {slices.map((row) => (
                      <Cell key={row.ad_format} fill={row.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    content={(props) => (
                      <DistributionTooltip {...props} total={total} formatValue={metricCount} />
                    )}
                  />
                </PieChart>
              </ChartContainer>
            ) : (
              <div className="flex h-36 items-center justify-center" aria-hidden="true">
                <div className="size-28 rounded-full border-[14px] border-muted" />
              </div>
            )}
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <strong title={metricCount(total)} className="text-xl font-semibold tabular-nums">
                {total.toLocaleString("en", { notation: "compact", maximumFractionDigits: 1 })}
              </strong>
              <span className="text-xs text-muted-foreground">{metric}</span>
            </div>
          </div>
          <div className="min-w-0 flex-1 basis-32">
            {total === 0 && (
              <p className="mb-3 text-xs text-muted-foreground">
                No {metric} recorded in this period.
              </p>
            )}
            <ul aria-label="Format totals" className="space-y-3">
              {formats.map((row) => (
                <li key={row.ad_format} className="flex min-w-0 items-start gap-2 text-xs">
                  <span
                    aria-hidden="true"
                    className="mt-1 size-2 shrink-0 rounded-full"
                    style={{ background: row.color }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-medium" title={row.name}>
                        {row.name}
                      </span>
                      <strong
                        aria-label={`${metricCount(row.value)} ${metric}`}
                        className="shrink-0 font-medium tabular-nums"
                      >
                        {metricCount(row.value)}
                      </strong>
                    </div>
                    <p className="mt-1 text-muted-foreground tabular-nums">
                      {total > 0 ? `${((100 * row.value) / total).toFixed(1)}%` : "—"} ·{" "}
                      {row.ctr === null ? "CTR unavailable" : `${metricPercent(row.ctr)} CTR`}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <p
          style={{ minHeight: compact ? 176 : 192 }}
          className="flex items-center justify-center px-4 text-center text-sm text-muted-foreground"
        >
          Format comparisons appear after your first delivery measurements.
        </p>
      )}
    </section>
  );
}

export function PartnerPerformance({
  data,
  compact = false,
  performanceHref,
}: {
  data: PerformanceReport;
  compact?: boolean;
  performanceHref?: string;
}) {
  const rows = performanceDays(data);
  return (
    <section aria-label="Ad performance" className="space-y-3">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight">Ad performance</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {data.start} to {data.end} · UTC
          </p>
        </div>
        {compact && performanceHref ? (
          <Button variant="outline" size="sm" asChild>
            <Link href={performanceHref}>View performance</Link>
          </Button>
        ) : !compact ? (
          <Button variant="outline" size="sm" asChild>
            <a
              href={`data:text/csv;charset=utf-8,${encodeURIComponent(performanceCsv(rows))}`}
              download={`devfeed-performance-${data.start}-${data.end}.csv`}
            >
              <Download size={14} aria-hidden="true" /> Export daily CSV
            </a>
          </Button>
        ) : null}
      </header>
      <Metrics data={data} />
      <div className="grid min-w-0 gap-3 xl:grid-cols-3">
        <DeliveryTrend rows={rows} compact={compact} />
        <section aria-label="Click-through trend" className="min-w-0 rounded-lg border bg-card p-4">
          <header className="mb-3">
            <h3 className="text-sm font-semibold">Click-through rate</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Daily clicks divided by impressions
            </p>
          </header>
          <TrendChart
            rows={rows}
            metric="ctr"
            label="Click-through rate"
            height={compact ? 176 : 192}
          />
        </section>
        <FormatPerformance data={data} compact={compact} />
      </div>
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
