"use client";

import type { ReactNode } from "react";
import { ResponsiveContainer, type TooltipContentProps } from "recharts";
import { cn } from "@/lib/utils";

/** Shared sizing and tooltip treatment for admin charts. */
export function ChartContainer({
  children,
  label,
  className,
  height,
}: {
  children: ReactNode;
  label: string;
  className?: string;
  height?: number;
}) {
  return (
    <figure
      aria-label={label}
      style={height === undefined ? undefined : { height }}
      className={cn(
        "h-64 min-w-0 text-xs [&_text]:fill-muted-foreground [&_.recharts-surface]:outline-ring [&_.recharts-tooltip-wrapper]:z-20",
        className,
      )}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        minWidth={0}
        initialDimension={{ width: 600, height: height ?? 256 }}
      >
        {children}
      </ResponsiveContainer>
    </figure>
  );
}

export function ChartTooltip({
  active,
  payload,
  label,
  formatLabel,
  formatValue,
}: Pick<TooltipContentProps, "active" | "payload" | "label"> & {
  formatLabel?: (label: string) => string;
  formatValue?: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="w-max max-w-64 rounded-lg border bg-popover px-3 py-2.5 text-xs text-popover-foreground shadow-md"
    >
      {label != null && (
        <p className="mb-2 font-medium break-words">
          {formatLabel ? formatLabel(String(label)) : label}
        </p>
      )}
      <div className="space-y-1.5">
        {payload.map((item) => (
          <div key={String(item.dataKey)} className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-2 shrink-0 rounded-full"
              style={{ background: item.color }}
            />
            <span className="min-w-0 flex-1 break-words text-muted-foreground">{item.name}</span>
            <span className="ml-auto shrink-0 pl-4 font-medium whitespace-nowrap tabular-nums">
              {formatValue
                ? formatValue(Number(item.value))
                : Number(item.value).toLocaleString("en")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Keep donut labels separate from values and use one opaque surface above center totals. */
export function DistributionTooltip({
  active,
  payload,
  total,
  formatValue = (value: number) => value.toLocaleString("en"),
}: Pick<TooltipContentProps, "active" | "payload"> & {
  total: number;
  formatValue?: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const value = Number(payload[0].value);
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="w-max max-w-64 rounded-lg border bg-popover px-3 py-2.5 text-xs text-popover-foreground shadow-md"
    >
      <p className="mb-1.5 font-medium break-words">{payload[0].name}</p>
      <div className="flex items-baseline gap-3 whitespace-nowrap tabular-nums">
        <strong className="font-semibold">{formatValue(value)}</strong>
        <span className="text-muted-foreground">
          {total > 0 ? ((100 * value) / total).toFixed(1) : "0.0"}%
        </span>
      </div>
    </div>
  );
}
