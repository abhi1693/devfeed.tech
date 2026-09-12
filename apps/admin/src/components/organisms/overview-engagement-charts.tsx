"use client";

import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import { InfoTooltip } from "@/components/molecules/info-tooltip";
import type { AdminOverview } from "@/lib/api/generated/models";
import { formatCompactCount } from "@/lib/format-count";

const shortDate = (date: string) =>
  new Date(`${date}T00:00:00Z`).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
const decimal = (value: number) => value.toLocaleString("en", { maximumFractionDigits: 1 });
const colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)"];
function Card({
  title,
  help,
  summary,
  children,
}: {
  title: string;
  help: string;
  summary: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-lg border bg-card p-5">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <InfoTooltip label={title}>{help}</InfoTooltip>
      </div>
      <p className="mt-2 mb-4 text-xs text-muted-foreground">{summary}</p>
      {children}
    </section>
  );
}
function Axes({ fraction = false }: { fraction?: boolean }) {
  return (
    <>
      <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
      <XAxis
        dataKey="date"
        tickFormatter={shortDate}
        tickLine={false}
        axisLine={false}
        minTickGap={40}
        tickMargin={10}
      />
      <YAxis
        tickFormatter={fraction ? decimal : formatCompactCount}
        allowDecimals={fraction}
        tickLine={false}
        axisLine={false}
        width={42}
      />
    </>
  );
}

export function OverviewEngagementCharts({ data }: { data: AdminOverview }) {
  const activity = data.insights?.reader_activity ?? [];
  const rows = activity.map((day) => ({
    ...day,
    depth: day.readers && day.opens != null ? day.opens / day.readers : null,
  }));
  const known = rows.filter((day) => day.readers != null);
  const readerDays = known.reduce((sum, day) => sum + day.readers!, 0);
  const multiDays = known.reduce((sum, day) => sum + (day.multi_article_readers ?? 0), 0);
  const depthDays = known.filter((day) => day.opens != null);
  const depthReaders = depthDays.reduce((sum, day) => sum + day.readers!, 0);
  const clicks = depthDays.reduce((sum, day) => sum + day.opens!, 0);
  const adoption = data.insights?.adoption;
  const accounts = adoption?.accounts ?? 0;
  const features = [
    { label: "Liked articles", value: adoption?.liking ?? 0 },
    { label: "Followed topics", value: adoption?.following_topics ?? 0 },
    { label: "Followed sources", value: adoption?.following_sources ?? 0 },
  ];
  return (
    <section aria-label="Reader engagement" className="min-w-0 space-y-4">
      <div>
        <h2 className="text-base font-semibold">Reader engagement</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Last {data.days} days · daily totals in UTC · today is in progress
        </p>
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card
          title="Readers opening articles"
          help="Estimated daily readers who clicked an original article. Signed-in accounts are deduplicated across devices; anonymous readers use a browser cookie. Signing in or clearing cookies can count someone again. Multiple articles means at least two distinct articles opened that day. This measures interest, not completed reads or returning users."
          summary={
            <>
              {readerDays
                ? `${decimal((100 * multiDays) / readerDays)}% of reader-days included multiple articles`
                : "No recorded readers in the available history"}
              {known.length < rows.length && " · missing history appears as gaps"}
            </>
          }
        >
          <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>
              <i className="mr-2 inline-block size-2 rounded-sm bg-chart-1" />
              All readers
            </span>
            <span>
              <i className="mr-2 inline-block size-2 rounded-sm bg-chart-2" />
              Opened multiple articles
            </span>
          </div>
          {readerDays > 0 ? (
            <ChartContainer
              label="Daily readers and readers opening multiple articles"
              className="h-48"
            >
              <LineChart data={rows} accessibilityLayer margin={{ right: 12, top: 8, bottom: 5 }}>
                <Axes />
                <Tooltip
                  content={(props) => (
                    <ChartTooltip
                      {...props}
                      formatLabel={shortDate}
                      formatValue={formatCompactCount}
                    />
                  )}
                />
                <Line
                  dataKey="readers"
                  name="All readers"
                  stroke={colors[0]}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <Line
                  dataKey="multi_article_readers"
                  name="Opened multiple articles"
                  stroke={colors[1]}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ChartContainer>
          ) : (
            <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">
              No recorded readers in the available history.
            </p>
          )}
        </Card>
        <Card
          title="Clicks per reader"
          help="Original-article clicks divided by estimated readers each day. Each viewer/article/hour counts once; repeat clicks in another hour count again. The period average is weighted by reader-days, not unique people across the period. Days with no readers or unavailable data are gaps. This is not time spent reading."
          summary={
            depthReaders
              ? `${decimal(clicks / depthReaders)} clicks per reader-day on average`
              : "No recorded clicks per reader yet"
          }
        >
          {depthReaders > 0 ? (
            <ChartContainer label="Daily original article clicks per reader" className="h-56">
              <LineChart data={rows} accessibilityLayer margin={{ right: 12, top: 8, bottom: 5 }}>
                <Axes fraction />
                <Tooltip
                  content={(props) => (
                    <ChartTooltip {...props} formatLabel={shortDate} formatValue={decimal} />
                  )}
                />
                <Line
                  dataKey="depth"
                  name="Clicks per reader"
                  stroke={colors[1]}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ChartContainer>
          ) : (
            <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">
              No recorded clicks per reader in the available history.
            </p>
          )}
        </Card>
        <Card
          title="New accounts"
          help="Accounts created each day. Historical snapshots can include accounts since deleted. Anonymous visitors are not included. This shows sign-up growth, not a visitor-to-sign-up conversion rate."
          summary={`${formatCompactCount(activity.reduce((sum, day) => sum + (day.accounts ?? 0), 0))} accounts created in this period`}
        >
          {activity.some((day) => day.accounts) ? (
            <ChartContainer label="Daily new accounts" className="h-48">
              <BarChart data={rows} accessibilityLayer margin={{ right: 12, top: 8, bottom: 5 }}>
                <Axes />
                <Tooltip
                  content={(props) => (
                    <ChartTooltip
                      {...props}
                      formatLabel={shortDate}
                      formatValue={formatCompactCount}
                    />
                  )}
                />
                <Bar
                  dataKey="accounts"
                  name="New accounts"
                  fill={colors[2]}
                  maxBarSize={28}
                  radius={[3, 3, 0, 0]}
                  isAnimationActive={false}
                />
              </BarChart>
            </ChartContainer>
          ) : (
            <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">
              No accounts were created in this period.
            </p>
          )}
        </Card>
        <Card
          title="Likes and follows"
          help="Current share of all existing accounts with at least one article like, topic follow, or source follow. Each account counts once per feature; groups overlap and must not be added. Removing all likes or follows removes that account from the corresponding count. This snapshot does not change with the date range."
          summary={`Current adoption · ${formatCompactCount(accounts)} total accounts`}
        >
          {accounts ? (
            <ul aria-label="Account feature adoption" className="space-y-6 py-3">
              {features.map((feature, index) => (
                <li key={feature.label}>
                  <div className="mb-2 flex justify-between gap-3 text-xs">
                    <span>{feature.label}</span>
                    <span className="tabular-nums">
                      {formatCompactCount(feature.value)} accounts ·{" "}
                      {decimal((100 * feature.value) / accounts)}%
                    </span>
                  </div>
                  <div
                    role="meter"
                    aria-label={feature.label}
                    aria-valuenow={feature.value}
                    aria-valuemin={0}
                    aria-valuemax={accounts}
                    aria-valuetext={`${feature.value} of ${accounts} accounts`}
                    className="h-3 overflow-hidden rounded-sm bg-muted"
                  >
                    <div
                      className="h-full rounded-sm"
                      style={{
                        width: `${(100 * feature.value) / accounts}%`,
                        background: colors[index],
                      }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">
              No accounts yet.
            </p>
          )}
        </Card>
      </div>
    </section>
  );
}
