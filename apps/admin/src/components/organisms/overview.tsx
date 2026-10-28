"use client";

import { useState } from "react";
import { DateRangePicker, defaultDateRange, type DateRange } from "@devfeed/ui/date-range-picker";
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
              <OverviewChart key={chart.id} chart={chart.id} range={range} />
            ))}
        </section>
      ))}
    </section>
  );
}
