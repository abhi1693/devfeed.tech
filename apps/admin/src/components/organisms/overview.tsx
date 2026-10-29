"use client";

import { useEffect, useState } from "react";
import {
  DateRangePicker,
  dateRangeBounds,
  defaultDateRange,
  type DateRange,
} from "@devfeed/ui/date-range-picker";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import { OverviewChart } from "./overview-chart";
import { overviewCharts } from "@/lib/overview-charts";

const sections = [
  { name: "publishing", title: "Publishing" },
  { name: "operations", title: "Needs attention" },
  { name: "audience", title: "Audience" },
  { name: "processing", title: "Processing" },
  { name: "inference", title: "AI usage" },
  { name: "coverage", title: "Sources and topics" },
] as const;

export function Overview({ initialRange = defaultDateRange }: { initialRange?: DateRange }) {
  const [range, setRange] = useState(initialRange);
  const [today, setToday] = useState(() => new Date().toISOString().slice(0, 10));
  useEffect(
    () =>
      runWhenPageActive(
        () => {
          let timer: ReturnType<typeof setTimeout>;
          const update = () => {
            const now = new Date();
            setToday(now.toISOString().slice(0, 10));
            const next = new Date(now);
            next.setUTCHours(24, 0, 0, 0);
            timer = setTimeout(update, Math.max(1, next.getTime() - now.getTime()));
          };
          update();
          return () => clearTimeout(timer);
        },
        { requireFocus: false },
      ),
    [],
  );
  const bounds = dateRangeBounds(range, new Date(`${today}T00:00:00Z`));
  const requestedRange = bounds ? { from: bounds.start, to: bounds.end } : range;
  function changeRange(next: DateRange) {
    setRange(next);
    const url = new URL(window.location.href);
    for (const key of ["from", "to"] as const) {
      if (next[key] === undefined) url.searchParams.delete(key);
      else url.searchParams.set(key, next[key]);
    }
    window.history.replaceState(window.history.state, "", url);
  }
  return (
    <section className="space-y-4" aria-label="Application overview">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <DateRangePicker value={range} onChange={changeRange} />
      </header>

      {sections.map((section) => (
        <section
          key={section.name}
          id={section.name}
          data-overview-section={section.name}
          aria-label={section.title}
          className="grid items-stretch gap-4 xl:grid-cols-2"
        >
          {overviewCharts
            .filter((chart) => chart.section === section.name)
            .map((chart) => (
              <OverviewChart key={chart.id} chart={chart.id} range={requestedRange} />
            ))}
        </section>
      ))}
    </section>
  );
}
