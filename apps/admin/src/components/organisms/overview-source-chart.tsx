"use client";

import Link from "next/link";
import { Cell, Pie, PieChart, Tooltip } from "recharts";
import type { OverviewSourcePerformance } from "@/lib/api/generated/models";
import { ChartContainer } from "@/components/atoms/chart";
import { formatCompactCount } from "@/lib/format-count";

export function OverviewSourceChart({
  rows,
  days,
  total,
}: {
  rows: OverviewSourcePerformance[];
  days: number;
  total: number;
}) {
  const leaders = rows
    .filter((row) => row.published > 0)
    .sort((a, b) => b.published - a.published || a.name.localeCompare(b.name))
    .slice(0, 5);
  if (!total)
    return (
      <p className="py-8 text-sm text-muted-foreground">
        No articles were published from active sources in this period.
      </p>
    );
  const other = Math.max(0, total - leaders.reduce((sum, row) => sum + row.published, 0));
  const slices = [
    ...leaders.map((row, index) => ({
      name: row.name,
      value: row.published,
      href: `/content/sources/${row.id}`,
      color: `var(--chart-${index + 1})`,
    })),
    ...(other
      ? [{ name: "Other sources", value: other, href: "/content/sources", color: "var(--chart-6)" }]
      : []),
  ];
  return (
    <div className="min-w-0">
      <p className="mb-3 text-xs text-muted-foreground">
        Last {days} days · share of source publications
      </p>
      <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-4">
        <div className="relative w-52 shrink-0">
          <ChartContainer label="Articles published by source" className="h-52">
            <PieChart accessibilityLayer>
              <Pie
                data={slices}
                dataKey="value"
                nameKey="name"
                innerRadius="70%"
                outerRadius="95%"
                stroke="var(--card)"
                strokeWidth={3}
                isAnimationActive={false}
              >
                {slices.map((slice) => (
                  <Cell key={slice.href} fill={slice.color} />
                ))}
              </Pie>
              <Tooltip
                content={({ active, payload }) =>
                  active && payload?.length ? (
                    <div className="rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
                      {payload[0].name}: {formatCompactCount(Number(payload[0].value))} ·{" "}
                      {((100 * Number(payload[0].value)) / total).toFixed(1)}%
                    </div>
                  ) : null
                }
              />
            </PieChart>
          </ChartContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <strong className="text-2xl font-semibold tabular-nums">
              {formatCompactCount(total)}
            </strong>
            <span className="text-xs text-muted-foreground">Source publications</span>
          </div>
        </div>
        <ul
          className="w-full min-w-0 max-w-md space-y-3 text-xs"
          aria-label="Source publication shares"
        >
          {slices.map((slice) => (
            <li key={slice.href} className="flex items-center gap-2">
              <span
                aria-hidden
                className="size-2.5 shrink-0 rounded-sm"
                style={{ background: slice.color }}
              />
              <Link
                href={slice.href}
                className="min-w-0 flex-1 truncate hover:underline"
                title={slice.name}
              >
                {slice.name}
              </Link>
              <strong className="font-medium tabular-nums">
                {formatCompactCount(slice.value)}
              </strong>
              <span className="w-12 text-right text-muted-foreground tabular-nums">
                {((100 * slice.value) / total).toFixed(1)}%
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
