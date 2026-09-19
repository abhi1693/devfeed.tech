"use client";

import { useCallback } from "react";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { Clock3, Database, Search, TrendingUp } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import { Metric } from "@/components/molecules/metric";
import { PageHeading } from "@/components/molecules/page-heading";
import { searchAnalyticsV1AdminSearchAnalyticsGet } from "@/lib/api/generated/admin";
import type { SearchAnalytics, SearchAnalyticsQuery } from "@/lib/api/generated/models";
import { formatCompactCount } from "@/lib/format-count";
import { useRequest } from "@/lib/use-request";

function QueryChart({
  title,
  description,
  queries,
  emptyMessage,
}: {
  title: string;
  description: string;
  queries: SearchAnalyticsQuery[];
  emptyMessage: string;
}) {
  const rows = queries.slice(0, 10).map((item) => ({
    name: item.query,
    count: item.count,
  }));

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length ? (
          <ChartContainer label={title} height={Math.max(256, rows.length * 44 + 48)}>
            <BarChart
              data={rows}
              layout="vertical"
              accessibilityLayer
              margin={{ top: 8, right: 8, left: 0, bottom: 8 }}
            >
              <CartesianGrid vertical stroke="var(--border)" strokeDasharray="3 4" />
              <XAxis
                type="number"
                tickFormatter={formatCompactCount}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                interval={0}
                tickLine={false}
                axisLine={false}
                width={144}
                tick={{ width: 136 }}
              />
              <Tooltip
                content={(props) => <ChartTooltip {...props} formatValue={formatCompactCount} />}
              />
              <Bar
                dataKey="count"
                name="Searches"
                fill="var(--chart-1)"
                maxBarSize={28}
                isAnimationActive={false}
              />
            </BarChart>
          </ChartContainer>
        ) : (
          <p className="flex min-h-64 items-center justify-center text-sm text-muted-foreground">
            {emptyMessage}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function formatUpdatedAt(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function SearchAnalyticsOverview() {
  const load = useCallback(
    (signal: AbortSignal) => searchAnalyticsV1AdminSearchAnalyticsGet({ limit: 50 }, { signal }),
    [],
  );
  const { data, error, loading, refreshing } = useRequest<SearchAnalytics>(
    "search-analytics",
    load,
    60_000,
  );

  const totalSearches = data?.popular_queries.reduce((total, item) => total + item.count, 0) ?? 0;
  const updated = data ? formatUpdatedAt(data.generated_at) : undefined;

  return (
    <div className="space-y-8">
      <PageHeading
        title="Search analytics"
        description="See the queries Typesense is aggregating for the reader search experience."
        leading={<Search className="size-6 text-muted-foreground" aria-hidden />}
      />

      {loading && <p className="text-sm text-muted-foreground">Loading search analytics…</p>}
      {error && !data && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">Search analytics could not be loaded.</p>
            <p className="mt-1 text-sm text-muted-foreground">Refresh the page and try again.</p>
          </CardContent>
        </Card>
      )}

      {data && (
        <>
          {!data.enabled && (
            <Card className="border-amber-500/50 bg-amber-500/5">
              <CardContent className="pt-6">
                <p className="font-medium">Typesense analytics is not enabled.</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Enable native search analytics in the search deployment to populate this page.
                </p>
              </CardContent>
            </Card>
          )}

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Metric
              label="Popular queries"
              value={data.popular_queries.length}
              description="Top aggregated searches"
              icon={<TrendingUp />}
            />
            <Metric
              label="No-result queries"
              value={data.nohits_queries.length}
              description="Searches with no matching articles"
              icon={<Search />}
            />
            <Metric
              label="Recorded searches"
              value={totalSearches}
              description="Across the queries shown"
              icon={<Database />}
            />
            <Metric
              label="Analytics rules"
              value={data.rules.length}
              description={`Flushes every ${data.flush_interval_seconds}s`}
              icon={<Clock3 />}
            />
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <QueryChart
              title="Popular queries"
              description="Most frequently searched terms captured by Typesense."
              queries={data.popular_queries}
              emptyMessage="No popular queries have been flushed yet."
            />
            <QueryChart
              title="No-result queries"
              description="Terms that readers searched for without a matching result."
              queries={data.nohits_queries}
              emptyMessage="No no-result queries have been flushed yet."
            />
          </div>

          <p className="text-xs text-muted-foreground">
            {refreshing ? "Refreshing…" : updated ? `Updated ${updated}` : "Waiting for data"}
          </p>
        </>
      )}
    </div>
  );
}
