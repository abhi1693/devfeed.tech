"use client";

import { useCallback, useState } from "react";
import { Bell, CheckCheck, MousePointerClick, Send, Users } from "lucide-react";
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/atoms/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/atoms/table";
import { Metric } from "@/components/molecules/metric";
import { PageHeading } from "@/components/molecules/page-heading";
import { adminPushAnalytics } from "@/lib/api/generated/admin";
import type { PushAnalytics, PushAnalyticsMetrics } from "@/lib/api/generated/models";
import { formatCompactCount } from "@/lib/format-count";
import { useRequest } from "@/lib/use-request";

const outcomes = [
  { key: "accepted", label: "Relay accepted", color: "var(--chart-1)" },
  { key: "displayed", label: "Reported displays", color: "var(--chart-2)" },
  { key: "clicked", label: "Reported clicks", color: "var(--chart-3)" },
  { key: "opened", label: "Reported opens", color: "var(--chart-4)" },
] as const;

function count(metrics: PushAnalyticsMetrics, key: keyof PushAnalyticsMetrics) {
  return metrics[key] ?? 0;
}

function clickRate(metrics: PushAnalyticsMetrics) {
  return count(metrics, "displayed") > 0 ? `${count(metrics, "click_rate").toFixed(1)}%` : "—";
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

function PushTrend({ data }: { data: PushAnalytics }) {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>Notification outcomes</CardTitle>
        <CardDescription>
          Grouped by the event’s publication date in UTC. Later outcomes update that same cohort.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {count(data.totals, "browser_deliveries") ? (
          <>
            <ChartContainer label="Notification outcomes by UTC publication date" className="h-72">
              <LineChart
                data={data.daily}
                accessibilityLayer
                margin={{ top: 12, right: 12, left: 0, bottom: 4 }}
              >
                <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
                <XAxis
                  dataKey="date"
                  tickFormatter={shortDate}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={40}
                />
                <YAxis
                  tickFormatter={formatCompactCount}
                  tickLine={false}
                  axisLine={false}
                  allowDecimals={false}
                  width={42}
                />
                <Tooltip
                  content={(props) => (
                    <ChartTooltip
                      {...props}
                      formatLabel={(date) => `${shortDate(date)} · UTC publication cohort`}
                      formatValue={formatCompactCount}
                    />
                  )}
                />
                {outcomes.map((outcome) => (
                  <Line
                    key={outcome.key}
                    type="monotone"
                    dataKey={outcome.key}
                    name={outcome.label}
                    stroke={outcome.color}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ChartContainer>
            <ul aria-label="Outcome chart legend" className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
              {outcomes.map((outcome) => (
                <li
                  key={outcome.key}
                  className="flex items-center gap-2 text-xs text-muted-foreground"
                >
                  <span
                    aria-hidden
                    className="size-2 rounded-full"
                    style={{ background: outcome.color }}
                  />
                  {outcome.label}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="flex min-h-64 items-center justify-center text-sm text-muted-foreground">
            No browser deliveries in this period.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function PushAnalyticsOverview() {
  const [days, setDays] = useState(30);
  const [revision, setRevision] = useState(0);
  const [checkedAt, setCheckedAt] = useState(0);
  const load = useCallback(
    async (signal: AbortSignal) => {
      void revision;
      const result = await adminPushAnalytics({ days }, { signal });
      if (!signal.aborted) setCheckedAt(Date.now());
      return result;
    },
    [days, revision],
  );
  const { data, error, loading, refreshing } = useRequest<PushAnalytics>(
    `push-analytics/${days}`,
    load,
    60_000,
  );
  const stale = data && checkedAt - new Date(data.generated_at).getTime() > 120_000;

  return (
    <div className="min-w-0 space-y-8" aria-busy={loading || refreshing}>
      <PageHeading
        title="Push analytics"
        description="Follow browser notification delivery and reported engagement."
        leading={<Bell className="size-6 text-muted-foreground" aria-hidden />}
      >
        <div
          className="flex rounded-lg border bg-muted/50 p-1"
          role="group"
          aria-label="Notification date range"
        >
          {[7, 30, 90].map((value) => (
            <Button
              key={value}
              variant={days === value ? "default" : "ghost"}
              size="sm"
              aria-pressed={days === value}
              onClick={() => setDays(value)}
            >
              {value} days
            </Button>
          ))}
        </div>
      </PageHeading>

      {loading && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading push analytics…
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 p-4 text-sm"
        >
          <p>
            Push analytics could not be updated.
            {data && " Showing the last successful result."}
          </p>
          <Button variant="outline" size="sm" onClick={() => setRevision((value) => value + 1)}>
            Try again
          </Button>
        </div>
      )}

      {data && (
        <>
          {!data.enabled && (
            <Card className="border-amber-500/50 bg-amber-500/5">
              <CardContent>
                <p className="font-medium">Browser push is disabled.</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Historical outcomes remain available here.
                </p>
              </CardContent>
            </Card>
          )}

          <section
            aria-label="Notification reach"
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
          >
            <Metric
              label="Enabled accounts"
              value={data.enabled_accounts}
              description="Current stored registrations; live sessions may expire earlier"
              icon={<Users />}
            />
            <Metric
              label="Enabled browsers"
              value={data.enabled_subscriptions}
              description="Current registrations within their authorization deadline"
              icon={<Bell />}
            />
            <Metric
              label="Published events"
              value={count(data.totals, "published_events")}
              description={`Created in the last ${data.days} UTC calendar days`}
              icon={<Send />}
            />
            <Metric
              label="Recipient accounts"
              value={count(data.totals, "recipient_accounts")}
              description="Distinct accounts with a browser delivery in this cohort"
              icon={<Users />}
            />
          </section>

          <section
            aria-label="Notification engagement"
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
          >
            <Metric
              label="Relay accepted"
              value={count(data.totals, "accepted")}
              description="Push relay accepted the encrypted request"
              icon={<Send />}
            />
            <Metric
              label="Reported displays"
              value={count(data.totals, "displayed")}
              description="Browser reported a successful notification display"
              icon={<Bell />}
            />
            <Metric
              label="Reported clicks"
              value={count(data.totals, "clicked")}
              description="Browser reported notification clicks"
              icon={<MousePointerClick />}
            />
            <Metric
              label="Reported opens"
              value={count(data.totals, "opened")}
              description="Successful notification navigation; does not indicate article reading"
              icon={<CheckCheck />}
            />
          </section>

          <PushTrend data={data} />

          <div className="grid min-w-0 gap-6 xl:grid-cols-[1fr_2fr]">
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>Delivery status</CardTitle>
                <CardDescription>
                  Current outcomes for browser deliveries in the selected cohort.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-5 text-sm">
                  {[
                    ["Browser deliveries", count(data.totals, "browser_deliveries")],
                    ["Failed", count(data.totals, "failed")],
                    ["Skipped", count(data.totals, "skipped")],
                    ["Queued", count(data.totals, "queued")],
                    ["Running", count(data.totals, "running")],
                    ["Retry attempts", count(data.totals, "retries")],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="mt-1 text-xl font-semibold tabular-nums">
                        {value.toLocaleString("en")}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-6 border-t pt-5">
                  <p className="text-sm text-muted-foreground">
                    Click rate among reported displays
                  </p>
                  <p className="mt-1 text-2xl font-semibold tabular-nums">
                    {clickRate(data.totals)}
                  </p>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    {count(data.totals, "displayed")
                      ? "Only deliveries with both a display and a click report count toward this rate."
                      : "No reported displays to calculate a click rate."}
                  </p>
                </div>
              </CardContent>
            </Card>

            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>Notification types</CardTitle>
                <CardDescription>
                  Published types and their browser outcomes in this period.
                </CardDescription>
              </CardHeader>
              <CardContent className="min-w-0">
                {data.by_kind.length ? (
                  <Table aria-label="Notification outcomes by type">
                    <TableHeader>
                      <TableRow>
                        {[
                          "Type",
                          "Events",
                          "Browsers",
                          "Accepted",
                          "Displays",
                          "Clicks",
                          "Opens",
                          "Click rate",
                        ].map((label) => (
                          <TableHead
                            key={label}
                            scope="col"
                            className={label === "Type" ? "" : "text-right"}
                          >
                            {label}
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.by_kind.map((kind) => (
                        <TableRow key={kind.kind}>
                          <TableHead scope="row">
                            {kind.kind === "daily_must_read" ? "Daily Must Read" : kind.kind}
                          </TableHead>
                          {[
                            "published_events",
                            "browser_deliveries",
                            "accepted",
                            "displayed",
                            "clicked",
                            "opened",
                          ].map((key) => (
                            <TableCell key={key} className="text-right tabular-nums">
                              {count(kind, key as keyof PushAnalyticsMetrics).toLocaleString("en")}
                            </TableCell>
                          ))}
                          <TableCell className="text-right tabular-nums">
                            {clickRate(kind)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No notification events published in this period.
                  </p>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>How to read these numbers</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
              <p>
                All period totals and charts follow the event’s publication date in UTC, including
                outcomes reported later. Recipient accounts are distinct within each cohort and may
                appear on more than one day.
              </p>
              <p>
                Relay acceptance does not confirm browser display. Displays, clicks, and opens are
                browser reports; missing reports remain unconfirmed. An open means navigation
                succeeded, and does not mean the article was read.
              </p>
              <p>
                Skipped jobs completed without recorded relay acceptance, for example after consent
                or session expiry. Retry attempts count attempts beyond the first for each browser
                delivery.
              </p>
            </CardContent>
          </Card>

          <p
            role="status"
            aria-label="Push analytics refresh status"
            className={`text-xs ${error || stale ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}`}
          >
            {refreshing
              ? "Refreshing…"
              : `${error || stale ? "Stale data · " : ""}Updated ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(data.generated_at))}`}
          </p>
        </>
      )}
    </div>
  );
}
