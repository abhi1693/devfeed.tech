export type PerformanceReport = {
  start: string;
  end: string;
  totals: { impressions: number; clicks: number; ctr: number | null; measured_days: number };
  trend: { day: string; impressions: number; clicks: number }[];
  format_totals?: {
    ad_format: string;
    impressions: number;
    clicks: number;
    ctr: number | null;
    measured_days: number;
  }[];
  last_updated_at: string | null;
};

export type PerformanceDay = {
  day: string;
  impressions: number | null;
  clicks: number | null;
  ctr: number | null;
};

/** Missing ledger days stay unknown; a recorded zero is a measurement. */
export function performanceDays(
  data: Pick<PerformanceReport, "start" | "end" | "trend">,
): PerformanceDay[] {
  const measured = new Map(data.trend.map((row) => [row.day, row]));
  const first = Date.parse(`${data.start}T00:00:00Z`);
  const last = Date.parse(`${data.end}T00:00:00Z`);
  if (!Number.isFinite(first) || !Number.isFinite(last) || last < first) return [];
  const rows: PerformanceDay[] = [];
  for (let time = first; time <= last && rows.length < 365; time += 86_400_000) {
    const day = new Date(time).toISOString().slice(0, 10);
    const row = measured.get(day);
    rows.push({
      day,
      impressions: row?.impressions ?? null,
      clicks: row?.clicks ?? null,
      ctr: row && row.impressions > 0 ? (100 * row.clicks) / row.impressions : null,
    });
  }
  return rows;
}

export function performanceCsv(rows: PerformanceDay[]) {
  return [
    "Date (UTC),Impressions,Clicks,CTR (%)",
    ...rows.map((row) =>
      [row.day, row.impressions ?? "", row.clicks ?? "", row.ctr?.toFixed(2) ?? ""].join(","),
    ),
  ].join("\r\n");
}

export const metricCount = (value: number) => value.toLocaleString("en");
export const metricPercent = (value: number) => `${value.toFixed(2)}%`;
export const shortMetricDate = (day: string) =>
  new Date(`${day}T00:00:00Z`).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
