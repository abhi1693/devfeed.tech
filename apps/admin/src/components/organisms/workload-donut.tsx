"use client";
import Link from "next/link";

export function WorkloadDonut({
  rows,
  queuedDelta,
  runningDelta,
  compared,
}: {
  rows: { kind: string; label: string; href: string; queued: number; running: number }[];
  queuedDelta: number;
  runningDelta: number;
  compared: boolean;
}) {
  const running = rows.reduce((sum, row) => sum + row.running, 0);
  const queued = rows.reduce((sum, row) => sum + row.queued, 0);
  const colors = [
    "var(--chart-1)",
    "var(--chart-2)",
    "var(--chart-3)",
    "var(--chart-4)",
    "var(--chart-5)",
    "var(--chart-6)",
    "#9c755f",
    "#b24d8c",
  ];
  const circumference = 2 * Math.PI * 62;
  const delta = (value: number) =>
    value
      ? `${value > 0 ? "+" : "−"}${Math.abs(value).toLocaleString("en")} since last check`
      : compared
        ? "No change since last check"
        : "Initial observation";
  return (
    <div className="grid min-w-0 items-center gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
      <div>
        <figure className="relative mx-auto size-44" aria-label="Running jobs by type">
          <svg viewBox="0 0 176 176" className="size-full" aria-hidden="true">
            <circle cx="88" cy="88" r="62" fill="none" stroke="var(--muted)" strokeWidth="20" />
            {rows.map((row, index) => {
              const length = running ? (row.running / running) * circumference : 0;
              const start = running
                ? (rows.slice(0, index).reduce((sum, item) => sum + item.running, 0) / running) *
                  circumference
                : 0;
              return row.running > 0 ? (
                <circle
                  key={row.kind}
                  cx="88"
                  cy="88"
                  r="62"
                  fill="none"
                  stroke={colors[index]}
                  strokeWidth="20"
                  strokeDasharray={`${Math.max(0, length - (row.running === running ? 0 : 3))} ${circumference}`}
                  strokeDashoffset={-start}
                  transform="rotate(-90 88 88)"
                />
              ) : null;
            })}
          </svg>
          <dl className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <dd className="text-3xl font-semibold tabular-nums">{running.toLocaleString("en")}</dd>
            <dt className="text-xs text-muted-foreground">running now</dt>
          </dl>
        </figure>
        <p className="text-center text-xs text-muted-foreground">{delta(runningDelta)}</p>
        <dl className="mt-3 flex items-baseline justify-center gap-1.5">
          <dd className="text-lg font-semibold tabular-nums">{queued.toLocaleString("en")}</dd>
          <dt className="text-xs text-muted-foreground">queued</dt>
        </dl>
        <p className="mt-1 text-center text-xs text-muted-foreground">{delta(queuedDelta)}</p>
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
                      style={{ background: colors[index] }}
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
