"use client";

import Link from "next/link";
import type { OverviewSourcePerformance } from "@/lib/api/generated/models";
import { InfoTooltip } from "@/components/molecules/info-tooltip";

/** A shared linear scale makes source contributions comparable without hover. */
export function OverviewSourceChart({
  rows,
  days,
}: {
  rows: OverviewSourcePerformance[];
  days: number;
}) {
  const published = rows
    .filter((row) => row.published > 0)
    .sort((a, b) => b.published - a.published || a.name.localeCompare(b.name));
  if (!published.length)
    return (
      <p className="py-8 text-sm text-muted-foreground">
        No articles were published from active sources in this period.
      </p>
    );
  const maximum = published[0].published;
  return (
    <figure aria-label="Articles published by source" className="min-w-0">
      <figcaption className="mb-5 text-xs text-muted-foreground">
        Last {days} days · most published first
      </figcaption>
      <ol className="space-y-5" aria-label="Sources ranked by published articles">
        {published.map((row) => (
          <li
            key={row.id}
            className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)] sm:items-center sm:gap-6"
          >
            <div className="flex min-w-0 items-center gap-2">
              <Link
                href={`/content/sources/${row.id}`}
                className="min-w-0 break-words text-sm font-medium hover:underline"
              >
                {row.name}
              </Link>
              <span className="shrink-0">
                <InfoTooltip label={`${row.name} output`}>
                  {row.discovered.toLocaleString("en")} articles discovered in this period;{" "}
                  {row.followers.toLocaleString("en")} followers now. Publications can include
                  articles discovered earlier.
                </InfoTooltip>
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-3">
              <div aria-hidden className="min-w-0 flex-1">
                <div
                  className="h-6 min-w-1 rounded-r bg-chart-1"
                  style={{ width: `${(100 * row.published) / maximum}%` }}
                />
              </div>
              <span className="w-16 shrink-0 text-right text-sm font-semibold tabular-nums">
                {row.published.toLocaleString("en")}
                <span className="sr-only"> articles published</span>
              </span>
            </div>
          </li>
        ))}
      </ol>
    </figure>
  );
}
