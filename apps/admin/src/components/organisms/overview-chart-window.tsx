"use client";

import { useState } from "react";
import { CalendarDays, Focus } from "lucide-react";

/** Trim only the display range; totals and missing values always retain their original meaning. */
export function useChartWindow<T extends { date: string }>(rows: T[], active: (row: T) => boolean) {
  const [full, setFull] = useState(false);
  const first = rows.findIndex(active);
  const offset = first > 0 ? Math.min(Math.max(0, first - 1), Math.max(0, rows.length - 7)) : 0;
  const visible = full || !offset ? rows : rows.slice(offset);
  const control =
    offset > 0 ? (
      <button
        type="button"
        className="ml-auto inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        aria-label={full ? "Focus on activity" : "Show full period"}
        title={`${full ? "Full selected period. Focus on activity" : `Activity from ${visible[0].date}. Show full period`}. Totals always use the full selected period.`}
        onClick={() => setFull((value) => !value)}
      >
        {full ? (
          <Focus size={16} aria-hidden="true" />
        ) : (
          <CalendarDays size={16} aria-hidden="true" />
        )}
      </button>
    ) : null;
  return { rows: visible, control };
}
