"use client";
import Link from "next/link";
import { DonutChart, chartColors } from "@/components/molecules/overview-chart";

export function WorkloadDonut({
  rows,
}: {
  rows: { kind: string; label: string; href: string; queued: number; running: number }[];
}) {
  const running = rows.reduce((sum, row) => sum + row.running, 0);
  const queued = rows.reduce((sum, row) => sum + row.queued, 0);
  return (
    <div className="grid min-w-0 items-center gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
      <div>
        <DonutChart
          rows={rows.map((row) => ({ name: row.label, value: row.running }))}
          label="Running jobs by type"
        >
          <dl className="flex flex-col items-center justify-center">
            <dt className="order-2 text-xs text-muted-foreground">running now</dt>
            <dd className="order-1 text-3xl font-semibold tabular-nums">
              {running.toLocaleString("en")}
            </dd>
          </dl>
        </DonutChart>
        <dl className="mt-3 flex items-baseline justify-center gap-1.5">
          <dt className="order-2 text-xs text-muted-foreground">queued</dt>
          <dd className="order-1 text-lg font-semibold tabular-nums">
            {queued.toLocaleString("en")}
          </dd>
        </dl>
      </div>
      <div className="min-w-0">
        <table className="w-full text-xs" aria-label="Workload counts by job type">
          <thead>
            <tr className="border-b text-muted-foreground">
              <th className="pb-2 text-left font-normal">Job type</th>
              <th className="pb-2 pl-2 text-right font-normal">Queued</th>
              <th className="pb-2 pl-2 text-right font-normal">Running</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={row.kind}
                className={row.queued || row.running ? "" : "text-muted-foreground"}
              >
                <th className="py-2 text-left font-normal">
                  <Link href={row.href} className="inline-flex items-center gap-2 hover:underline">
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: chartColors[index % chartColors.length] }}
                      aria-hidden="true"
                    />
                    {row.label}
                  </Link>
                </th>
                <td className="pl-2 text-right tabular-nums">{row.queued.toLocaleString("en")}</td>
                <td className="pl-2 text-right tabular-nums">{row.running.toLocaleString("en")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!running && (
          <p className="mt-2 text-xs text-muted-foreground">
            {queued
              ? "Jobs are waiting; none are running."
              : "All queues are clear. No jobs waiting or running."}
          </p>
        )}
      </div>
    </div>
  );
}
