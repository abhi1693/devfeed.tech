"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@devfeed/ui/button";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminAutomationRecover } from "@/lib/api/generated/admin";
import { notify, notifyFailure } from "@/lib/notifications";
import { CircleCheck } from "lucide-react";
import {
  ChartCard,
  ChartChoice,
  TimeSeriesChart,
  MetricSparkline,
  MetricStat,
  BreakdownChart,
  DonutChart,
  EmptyChart,
} from "@/components/molecules/overview-chart";
import type {
  OverviewChartReport,
  AutomationBlocker,
  RecoveryTarget,
} from "@/lib/api/generated/models";
import { humanize } from "@/lib/resources";
import { chartDefinition, type ChartName } from "@/lib/overview-charts";
import { useChartPlaceholder } from "@/components/molecules/overview-chart-placeholder";
import { WorkloadDonut } from "./workload-donut";

type ChartData<K extends ChartName> = Extract<OverviewChartReport, { chart: K }>["data"];
type OperationsData = ChartData<"attention">;
type PublishingData = ChartData<"publication-rate"> & ChartData<"publication-latency">;
type AudienceData = ChartData<"audience-activity"> & ChartData<"personalization">;
type ProcessingData = ChartData<"queue-pressure"> &
  ChartData<"throughput"> &
  ChartData<"job-outcomes">;
type InferenceData = ChartData<"ai-activity"> & ChartData<"ai-breakdown">;
type CoverageData = ChartData<"source-output"> & ChartData<"topic-coverage">;

const count = (value: number | undefined | null) =>
  value == null ? "—" : value.toLocaleString("en", { maximumFractionDigits: 0 });
function duration(seconds: number | undefined | null) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} hr`;
}
function Operations({ data, onChange }: { data: OperationsData; onChange: () => void }) {
  const admin = useAdmin();
  const placeholder = useChartPlaceholder();
  const [busy, setBusy] = useState<string | null>(null);
  const actionLabels = {
    enrich: "Fetch article text",
    analyze: "Analyze again",
    evaluate: "Evaluate source policy",
  };
  async function recover(group: AutomationBlocker, target: RecoveryTarget) {
    if (!group.action || busy) return;
    setBusy(target.id);
    try {
      const result = await adminAutomationRecover(
        target.id,
        group.action,
        { expected_revision: target.revision ?? 0 },
        { headers: { "X-CSRF-Token": admin.csrf_token } },
      );
      const messages: Record<string, string> = {
        queued: "Recovery job queued",
        published: "Article published by its source policy",
        rejected: "Article rejected by its publication policy",
        would_publish: "Preview passed; publication remains in preview mode",
      };
      if (messages[result.status]) notify.success(messages[result.status]);
      else notify.warning("Article still needs review");
      onChange();
    } catch (failure) {
      notifyFailure(failure, "Could not complete recovery");
    } finally {
      setBusy(null);
    }
  }
  const links = {
    articles: "/content/articles?review_status=pending",
    sources: "/content/sources?approval_status=pending",
    topics: "/taxonomy/topics?view=proposals&status=pending",
    relationships: "/taxonomy/relationships/proposals?status=pending",
  };
  const reviews = (data.reviews ?? []).filter((row) => (row.count ?? 0) > 0);
  const blockers = (data.blockers ?? []).filter((row) => (row.count ?? 0) > 0);
  if (!placeholder && !reviews.length && !blockers.length && !data.source_failures) {
    return (
      <section
        data-overview-card="attention"
        aria-label="Review and recovery"
        className="flex min-h-14 items-center gap-2 rounded-lg border bg-card px-4 py-3 text-sm"
      >
        <CircleCheck aria-hidden className="size-4 text-emerald-600 dark:text-emerald-400" />
        <h3 className="font-medium">All clear</h3>
        <span className="text-xs text-muted-foreground">No reviews or blocked work</span>
      </section>
    );
  }
  function targetRow(group: AutomationBlocker, target: RecoveryTarget) {
    return (
      <li key={target.id} className="flex min-w-0 items-center justify-between gap-2">
        <Link
          className="min-w-0 truncate text-xs text-blue-700 hover:underline dark:text-blue-400"
          href={
            target.kind === "article"
              ? `/content/articles/${target.id}`
              : target.kind === "topic-proposal"
                ? `/taxonomy/topics/proposals/${target.id}`
                : `/taxonomy/relationships/proposals/${target.id}`
          }
        >
          {target.title}
        </Link>
        {target.kind === "article" && group.action && !data.full_automation && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            loading={busy === target.id}
            onClick={() => void recover(group, target)}
          >
            {actionLabels[group.action]}
          </Button>
        )}
      </li>
    );
  }
  return (
    <ChartCard {...chartDefinition("attention")}>
      <div className="flex flex-wrap gap-2">
        {reviews.map((row) => (
          <Link
            key={row.kind}
            href={links[row.kind]}
            aria-label={`${count(row.count)} ${humanize(row.kind)}`}
            className="inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs hover:bg-accent"
          >
            <strong className="tabular-nums">{count(row.count)}</strong>
            {humanize(row.kind)}
          </Link>
        ))}
        {!!data.source_failures && (
          <Link
            href="/content/sources"
            className="rounded-md border px-2.5 py-1.5 text-xs hover:bg-accent"
          >
            {count(data.source_failures)} source failures
          </Link>
        )}
      </div>
      {!!blockers.length && (
        <div className="grid gap-2 md:grid-cols-2">
          {blockers.map((group) => (
            <div key={group.code} className="min-w-0 rounded-md bg-muted/40 px-3 py-2">
              <p className="mb-1 flex justify-between gap-2 text-xs font-medium">
                <span>{group.label}</span>
                <span className="tabular-nums">{count(group.count)}</span>
              </p>
              <ul className="space-y-1">
                {group.targets.slice(0, 1).map((target) => targetRow(group, target))}
              </ul>
              {group.targets.length > 1 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    {group.targets.length - 1} more records
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {group.targets.slice(1).map((target) => targetRow(group, target))}
                  </ul>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </ChartCard>
  );
}

function Publishing({
  chart,
  data,
  granularity,
}: {
  data: PublishingData;
  chart: ChartName;
  granularity?: OverviewChartReport["granularity"];
}) {
  return (
    <>
      {chart === "publication-rate" && (
        <ChartCard {...chartDefinition("publication-rate")}>
          <div className="flex items-end justify-between gap-4">
            <MetricStat value={count(data.summary?.published)} label="published" />
            <MetricStat
              value={
                data.summary?.automatic_percent == null
                  ? "—"
                  : `${data.summary.automatic_percent.toFixed(1)}%`
              }
              label="automatic"
              align="end"
            />
          </div>
          <MetricSparkline
            granularity={granularity}
            rows={data.activity ?? []}
            series={[
              ["published", "Published"],
              ["automated", "Automatic"],
            ]}
            label="Publication output chart"
            kind="bar"
          />
        </ChartCard>
      )}
      {chart === "publication-latency" && (
        <ChartCard {...chartDefinition("publication-latency")}>
          <div className="flex items-end justify-between gap-4">
            <MetricStat value={duration(data.summary?.p50_seconds)} label="median" />
            <MetricStat
              value={duration(data.summary?.p95_seconds)}
              label="95th percentile"
              align="end"
            />
          </div>
          <MetricSparkline
            granularity={granularity}
            rows={data.activity ?? []}
            series={[
              ["p50_seconds", "Median seconds"],
              ["p95_seconds", "95th percentile seconds"],
            ]}
            label="Publication delay chart"
            unit="s"
          />
        </ChartCard>
      )}
    </>
  );
}
function Audience({
  chart,
  data,
  granularity,
}: {
  data: AudienceData;
  chart: ChartName;
  granularity?: OverviewChartReport["granularity"];
}) {
  const [metric, setMetric] = useState("opens");
  const names: Record<string, string> = {
    opens: "Article opens",
    estimated_readers: "Estimated readers",
    new_accounts: "New accounts",
  };
  const health = data.personalization;
  const states = (["ready", "refreshing", "expired", "pending", "not_needed"] as const).map(
    (name) => ({ name: humanize(name), value: health?.[name] ?? 0 }),
  );
  return (
    <>
      {chart === "audience-activity" && (
        <ChartCard {...chartDefinition("audience-activity")}>
          <ChartChoice
            label="Audience metric"
            value={metric}
            options={Object.entries(names)}
            onChange={setMetric}
          />
          <TimeSeriesChart
            granularity={granularity}
            rows={data.activity ?? []}
            series={[[metric, names[metric]]]}
            label="Audience activity chart"
          />
        </ChartCard>
      )}
      {chart === "personalization" && (
        <ChartCard {...chartDefinition("personalization")}>
          {states.some((row) => row.value) ? (
            <DonutChart rows={states} label="Personalized feed states" legend>
              <strong className="text-3xl font-semibold tabular-nums">
                {count(health?.ready)}
              </strong>
              <span className="text-xs text-muted-foreground">ready</span>
            </DonutChart>
          ) : (
            <EmptyChart />
          )}
          <p className="text-sm text-muted-foreground">
            {count(health?.overdue)} overdue · {count(health?.empty_with_interests)} prepared feeds
            empty despite interests
          </p>
        </ChartCard>
      )}
    </>
  );
}
const jobLinks: Record<string, string> = {
  ingestion: "/jobs/ingestion",
  article_enrichment: "/jobs/enrichment/articles",
  article_image: "/jobs/enrichment/images",
  source_enrichment: "/jobs/enrichment/sources",
  analysis: "/jobs/analysis/articles",
  topic_analysis: "/jobs/analysis/topics",
  research_verification: "/queues",
  notification: "/jobs/notifications",
};
function Processing({
  chart,
  data,
  granularity,
}: {
  data: ProcessingData;
  chart: ChartName;
  granularity?: OverviewChartReport["granularity"];
}) {
  const queues = (data.queues ?? []).map((row) => ({
    ...row,
    label: humanize(row.kind),
    href: jobLinks[row.kind] ?? "/queues",
    queued: row.queued ?? 0,
    running: row.running ?? 0,
  }));
  return (
    <>
      {chart === "queue-pressure" && (
        <ChartCard {...chartDefinition("queue-pressure")}>
          <WorkloadDonut rows={queues} />
        </ChartCard>
      )}
      {chart === "throughput" && (
        <ChartCard {...chartDefinition("throughput")}>
          <TimeSeriesChart
            granularity={granularity}
            rows={data.throughput ?? []}
            series={[
              ["articles_published", "Published"],
              ["article_analyses", "Applied analyses"],
              ["topic_decisions", "Topic decisions"],
            ]}
            label="Useful throughput chart"
            kind="bar"
          />
        </ChartCard>
      )}
      {chart === "job-outcomes" && (
        <ChartCard {...chartDefinition("job-outcomes")}>
          {(data.outcomes ?? []).length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs" aria-label="Job outcomes and duration">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="py-2 text-left">Job type</th>
                    <th className="p-2 text-right">Completed</th>
                    <th className="p-2 text-right">Failed</th>
                    <th className="p-2 text-right">Median</th>
                    <th className="p-2 text-right">95th</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.outcomes ?? []).map((row) => (
                    <tr key={row.kind} className="border-b last:border-0">
                      <th className="py-3 text-left font-normal">
                        <Link href={jobLinks[row.kind] ?? "/queues"} className="hover:underline">
                          {humanize(row.kind)}
                        </Link>
                      </th>
                      <td className="p-2 text-right tabular-nums">{count(row.completed)}</td>
                      <td
                        className={`p-2 text-right tabular-nums ${row.failed ? "font-semibold text-destructive" : ""}`}
                      >
                        {count(row.failed)}
                      </td>
                      <td className="p-2 text-right">{duration(row.p50_seconds)}</td>
                      <td className="p-2 text-right">{duration(row.p95_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyChart />
          )}
        </ChartCard>
      )}
    </>
  );
}
function Inference({
  chart,
  data,
  granularity,
}: {
  data: InferenceData;
  chart: ChartName;
  granularity?: OverviewChartReport["granularity"];
}) {
  const [metric, setMetric] = useState("tokens");
  const [dimension, setDimension] = useState("tasks");
  const series: Record<string, [string, string][]> = {
    tokens: [
      ["input_tokens", "Input tokens"],
      ["output_tokens", "Output tokens"],
    ],
    calls: [
      ["calls", "Calls"],
      ["failed", "Failed calls"],
    ],
    searches: [["web_searches", "Web searches"]],
    duration: [
      ["p50_ms", "Median ms"],
      ["p95_ms", "95th percentile ms"],
    ],
  };
  const rows =
    (dimension === "models" ? data.models : dimension === "efforts" ? data.efforts : data.tasks) ??
    [];
  return (
    <>
      {chart === "ai-activity" && (
        <ChartCard {...chartDefinition("ai-activity")}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <ChartChoice
              label="AI activity metric"
              value={metric}
              options={[
                ["tokens", "Tokens"],
                ["calls", "Calls and failures"],
                ["searches", "Web searches"],
                ["duration", "Estimated duration"],
              ]}
              onChange={setMetric}
            />
            <p className="text-xs text-muted-foreground">
              {count(data.summary?.calls)} calls · {count(data.summary?.unreported)} without usage
            </p>
          </div>
          <TimeSeriesChart
            granularity={granularity}
            rows={data.activity ?? []}
            series={series[metric]}
            label="AI activity chart"
            stacked={metric === "tokens"}
            kind={metric === "duration" ? "line" : "bar"}
            unit={metric === "duration" ? "ms" : ""}
          />
        </ChartCard>
      )}
      {chart === "ai-breakdown" && (
        <ChartCard {...chartDefinition("ai-breakdown")}>
          <ChartChoice
            label="AI usage breakdown"
            value={dimension}
            options={[
              ["tasks", "By task"],
              ["models", "By model"],
              ["efforts", "By reasoning effort"],
            ]}
            onChange={setDimension}
          />
          <BreakdownChart
            rows={rows}
            label="AI token breakdown chart"
            series={[
              ["input_tokens", "Input tokens"],
              ["output_tokens", "Output tokens"],
            ]}
          />
        </ChartCard>
      )}
    </>
  );
}
function Coverage({ data, chart }: { data: CoverageData; chart: ChartName }) {
  const rows = (data.sources ?? []).map((row) => ({
    ...row,
    name: row.name,
    publications: row.publications ?? 0,
  }));
  const shown = rows.reduce((total, row) => total + row.publications, 0);
  const other = Math.max(0, (data.source_publications_total ?? shown) - shown);
  return (
    <>
      {chart === "source-output" && (
        <ChartCard {...chartDefinition("source-output")}>
          <BreakdownChart
            rows={other ? [...rows, { name: "Other", publications: other }] : rows}
            label="Publications by source"
            series={[["publications", "Published articles"]]}
          />
          {rows.some((row) => row.consecutive_failures) && (
            <p className="text-xs text-muted-foreground">
              {rows
                .filter((row) => row.consecutive_failures)
                .map((row) => `${row.name}: ${row.consecutive_failures} consecutive fetch failures`)
                .join(" · ")}
            </p>
          )}
        </ChartCard>
      )}
      {chart === "topic-coverage" && (
        <ChartCard {...chartDefinition("topic-coverage")}>
          {(data.topics ?? []).length ? (
            <div className="overflow-x-auto">
              <table aria-label="Topic demand and coverage" className="w-full text-xs">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="py-2 text-left">Topic</th>
                    <th className="p-2 text-right">Following</th>
                    <th className="p-2 text-right">Inferred</th>
                    <th className="p-2 text-right">Published</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.topics ?? []).map((row) => (
                    <tr key={row.id} className="border-b last:border-0">
                      <th className="py-3 text-left font-normal">{row.name}</th>
                      <td className="p-2 text-right tabular-nums">{count(row.followers)}</td>
                      <td className="p-2 text-right tabular-nums">{count(row.inferred_users)}</td>
                      <td className="p-2 text-right tabular-nums">{count(row.publications)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyChart />
          )}
        </ChartCard>
      )}
    </>
  );
}
export function OverviewChartContent({
  report,
  onChange = () => {},
}: {
  report: OverviewChartReport;
  onChange?: () => void;
}) {
  const granularity = report.granularity;
  switch (report.chart) {
    case "attention":
      return <Operations data={report.data} onChange={onChange} />;
    case "publication-rate":
    case "publication-latency":
      return <Publishing chart={report.chart} data={report.data} granularity={granularity} />;
    case "audience-activity":
    case "personalization":
      return <Audience chart={report.chart} data={report.data} granularity={granularity} />;
    case "queue-pressure":
    case "throughput":
    case "job-outcomes":
      return <Processing chart={report.chart} data={report.data} granularity={granularity} />;
    case "ai-activity":
    case "ai-breakdown":
      return <Inference chart={report.chart} data={report.data} granularity={granularity} />;
    case "source-output":
    case "topic-coverage":
      return <Coverage chart={report.chart} data={report.data} />;
  }
}
