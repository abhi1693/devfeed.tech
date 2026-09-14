"use client";

import { useEffect, useState } from "react";
import { DateTime } from "@/components/molecules/date-time";
import type { PanelStatus } from "./overview-panel";

export function OverviewRefresh({
  statuses,
  days,
  refresh,
  expected,
}: {
  statuses: Record<string, PanelStatus>;
  days: number;
  refresh: number;
  expected: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  const current = Object.values(statuses).filter(
    (item) => item.days === days && item.refresh === refresh,
  );
  const pending = expected - current.filter((item) => !item.loading).length;
  const failed = current.filter((item) => item.failed).length;
  const stale = current.filter(
    (item) => !item.failed && item.generatedAt && now - Date.parse(item.generatedAt) > 5 * 60_000,
  ).length;
  const dates = current.flatMap((item) => (item.generatedAt ? [item.generatedAt] : [])).sort();
  const issues = Object.entries(statuses).filter(
    ([, item]) =>
      item.days === days &&
      item.refresh === refresh &&
      !item.loading &&
      (item.failed || (item.generatedAt && now - Date.parse(item.generatedAt) > 5 * 60_000)),
  );
  return (
    <div
      role="status"
      aria-label="Overview refresh status"
      className="text-sm text-muted-foreground"
    >
      {pending > 0 ? (
        `Refreshing ${pending} of ${expected} panels…`
      ) : failed || stale ? (
        `${failed + stale} ${failed + stale === 1 ? "panel needs" : "panels need"} attention · ${expected - failed - stale} up to date`
      ) : (
        <>Last refreshed · Data as of {dates[0] ? <DateTime value={dates[0]} /> : "—"}</>
      )}
      {pending > 0 && failed > 0 && ` · ${failed} failed`}
      {!pending && stale > 0 && " · Some results are over 5 minutes old"}
      {issues.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1" aria-label="Panels needing attention">
          {issues.map(([name]) => (
            <li key={name}>
              <a className="underline underline-offset-2" href={`#overview-panel-${name}`}>
                {name.replaceAll("-", " ")}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
