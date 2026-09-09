"use client";

import type { ReactNode } from "react";
import { ResponsiveContainer, type TooltipContentProps } from "recharts";
import { cn } from "@/lib/utils";

/** Shared sizing and tooltip treatment for admin charts. */
export function ChartContainer({ children, label, className }: { children: ReactNode; label: string; className?: string }) {
  return <figure aria-label={label} className={cn("h-64 min-w-0 text-xs [&_text]:fill-muted-foreground [&_.recharts-surface]:outline-ring", className)}>
    <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 600, height: 256 }}>
      {children}
    </ResponsiveContainer>
  </figure>;
}

export function ChartTooltip({ active, payload, label, formatLabel }: Pick<TooltipContentProps, "active" | "payload" | "label"> & { formatLabel?: (label: string) => string }) {
  if (!active || !payload?.length) return null;
  return <div role="status" aria-live="polite" aria-atomic="true" className="max-w-64 rounded-lg border bg-popover px-3 py-2.5 text-xs text-popover-foreground shadow-md">
    <p className="mb-2 font-medium break-words">{formatLabel ? formatLabel(String(label)) : label}</p>
    <div className="space-y-1.5">{payload.map(item => <div key={String(item.dataKey)} className="flex items-center gap-2">
      <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: item.color }} />
      <span className="text-muted-foreground">{item.name}</span>
      <span className="ml-auto pl-4 font-medium tabular-nums">{Number(item.value).toLocaleString("en")}</span>
    </div>)}</div>
  </div>;
}
