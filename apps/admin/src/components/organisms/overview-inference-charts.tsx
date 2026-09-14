"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip } from "@/components/atoms/chart";
import { InfoTooltip } from "@/components/molecules/info-tooltip";
import type { OverviewPanelAutomation as AutomationOverview } from "@/lib/api/generated/models";
import { formatCompactCount } from "@/lib/format-count";
import { humanize } from "@/lib/resources";
import { DistributionChart } from "./overview-breakdown-charts";

type Row = { name: string; [key: string]: string | number };
type Series = { key: string; name: string; color: string };
const colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];

function Bars({
  title,
  note,
  rows,
  series,
  stacked = false,
  horizontal = false,
}: {
  title: string;
  note: string;
  rows: Row[];
  series: Series[];
  stacked?: boolean;
  horizontal?: boolean;
}) {
  const populated = rows.some((row) => series.some(({ key }) => Number(row[key]) > 0));
  return (
    <section className="min-w-0" aria-label={title}>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <InfoTooltip label={title}>{note}</InfoTooltip>
      </div>
      <ul className="mb-3 flex flex-wrap gap-4 text-xs" aria-label={`${title} legend`}>
        {series.map((item) => (
          <li key={item.key} className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-2 shrink-0 rounded-sm"
              style={{ background: item.color }}
            />
            {item.name}
          </li>
        ))}
      </ul>
      {populated ? (
        <ChartContainer
          label={title}
          className="h-64"
          height={horizontal ? Math.max(256, rows.length * 48 + 40) : undefined}
        >
          <BarChart
            data={rows}
            layout={horizontal ? "vertical" : "horizontal"}
            accessibilityLayer
            margin={{ top: 8, right: 8, left: 0, bottom: horizontal ? 8 : 30 }}
          >
            <CartesianGrid
              vertical={horizontal}
              horizontal={!horizontal}
              stroke="var(--border)"
              strokeDasharray="3 4"
            />
            {horizontal ? (
              <>
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
              </>
            ) : (
              <>
                <XAxis
                  dataKey="name"
                  tickLine={false}
                  axisLine={false}
                  minTickGap={20}
                  height={48}
                />
                <YAxis
                  tickFormatter={formatCompactCount}
                  tickLine={false}
                  axisLine={false}
                  width={56}
                />
              </>
            )}
            <Tooltip
              content={(props) => <ChartTooltip {...props} formatValue={formatCompactCount} />}
            />
            {series.map((item) => (
              <Bar
                key={item.key}
                dataKey={item.key}
                name={item.name}
                fill={item.color}
                stackId={stacked ? "total" : undefined}
                maxBarSize={42}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ChartContainer>
      ) : (
        <p className="flex h-64 items-center justify-center text-sm text-muted-foreground">
          No recorded data in this period.
        </p>
      )}
    </section>
  );
}

export function OverviewInferenceCharts({
  data,
  chart,
}: {
  data?: AutomationOverview | null;
  chart?: string;
}) {
  const activity = data?.inference?.activity ?? [];
  const decisions = data?.topic_decisions;
  const days = new Map<string, Row>();
  const operations = new Map<string, Row>();
  const models = new Map<string, number>();
  const efforts = new Map<string, number>();
  for (const point of activity) {
    const tokens = point.input_tokens + point.output_tokens;
    models.set(point.model, (models.get(point.model) ?? 0) + tokens);
    efforts.set(point.effort, (efforts.get(point.effort) ?? 0) + point.calls);
    const day = days.get(point.date) ?? {
      name: point.date.slice(5),
      uncached: 0,
      cached: 0,
      output: 0,
      reasoning: 0,
      returned: 0,
      failed: 0,
      running: 0,
      searches: 0,
      duration: 0,
    };
    day.uncached = Number(day.uncached) + point.input_tokens - point.cached_input_tokens;
    day.cached = Number(day.cached) + point.cached_input_tokens;
    day.output = Number(day.output) + point.output_tokens - point.reasoning_tokens;
    day.reasoning = Number(day.reasoning) + point.reasoning_tokens;
    for (const key of ["returned", "failed", "running"] as const)
      day[key] = Number(day[key]) + point[key];
    day.searches = Number(day.searches) + point.web_searches;
    day.duration = Number(day.duration) + point.duration_ms;
    days.set(point.date, day);
    const operation = operations.get(point.operation) ?? {
      name: humanize(point.operation),
      input: 0,
      output: 0,
    };
    operation.input = Number(operation.input) + point.input_tokens;
    operation.output = Number(operation.output) + point.output_tokens;
    operations.set(point.operation, operation);
  }
  const daily = [...days.values()];
  const topicDays = (data?.inference?.topic_activity ?? []).map((day) => ({
    ...day,
    name: day.date.slice(5),
  }));
  const missing = activity.reduce((sum, point) => sum + point.unreported, 0);
  const tokens = [
    { key: "input", name: "Input (includes cached)", color: colors[0] },
    { key: "output", name: "Output (includes reasoning)", color: colors[1] },
  ];
  const first = data?.inference?.first_recorded_at;
  const throughput = data?.throughput;
  return (
    <section
      className={chart ? "space-y-6 rounded-lg border bg-card p-5" : "space-y-6 border-t pt-5"}
      aria-label="Inference and topic decisions"
    >
      {chart === "throughput" && <h3 className="text-base font-semibold">Pipeline throughput</h3>}
      {chart === "decision-efficiency" && (
        <h3 className="text-base font-semibold">Decision efficiency</h3>
      )}
      {(!chart ||
        [
          "inference-tokens",
          "tokens-by-task",
          "tokens-by-model",
          "reasoning-effort",
          "inference-outcomes",
          "web-searches",
          "inference-duration",
        ].includes(chart)) && (
        <div>
          {!chart && <h3 className="text-base font-semibold">Inference and topic decisions</h3>}
          <p className="mt-1 text-xs text-muted-foreground">
            Per-call telemetry{" "}
            {first
              ? `since ${new Date(first).toLocaleDateString("en", { timeZone: "UTC" })}`
              : "starts with v0.0.9"}
            . Grouped by call start date in UTC; earlier jobs are only represented in completed-job
            accounting. {missing} calls have no reported tokens. Tokens are usage measurements, not
            API bills or weekly quota percentages.
          </p>
        </div>
      )}
      {throughput && (!chart || chart === "throughput") && (
        <div className="grid gap-6 xl:grid-cols-2" aria-label="Pipeline throughput">
          <div className="min-w-0 space-y-4">
            <h4 className="text-sm font-medium">
              Worker capacity <span className="font-normal text-muted-foreground">· now</span>
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm" aria-label="Worker capacity">
                <thead>
                  <tr>
                    {["Worker pool", "Busy", "Idle", "Eligible"].map((label) => (
                      <th key={label} className="p-2 font-medium">
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(["topic", "article"] as const).map((kind) => (
                    <tr key={kind} className="border-t">
                      <th className="p-2 font-medium">{humanize(kind)} analysis</th>
                      {[
                        throughput[`busy_${kind}_workers`],
                        throughput[`idle_${kind}_workers`],
                        throughput[`eligible_${kind}_workers`],
                      ].map((value, index) => (
                        <td key={index} className="p-2 tabular-nums">
                          {throughput.capacity_observed && value != null ? value : "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-muted-foreground">
                {throughput.capacity_observed
                  ? `${throughput.shared_workers ?? 0} shared workers may appear in both pools. Busy means working on any subscribed queue; running jobs are tracked separately.`
                  : "Worker observation unavailable; this does not mean zero workers."}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              Topic work limit: {throughput.topic_admission_limit ?? 0} jobs. Shared workers are not
              additive capacity across queues. Busy shared workers are excluded from available
              capacity.
              {Boolean(throughput.cooldown_seconds) &&
                ` Provider cooldown: ${Math.ceil(throughput.cooldown_seconds! / 60)} minutes.`}
            </p>
            {(throughput.queues ?? []).map((queue) => (
              <p key={queue.kind} className="text-xs text-muted-foreground">
                {humanize(queue.kind)} queue: {queue.queued} queued, {queue.running} running; oldest
                due{" "}
                {queue.oldest_due_seconds == null
                  ? "—"
                  : `${Math.floor(queue.oldest_due_seconds / 60)} min`}
                ; median recorded processing{" "}
                {queue.median_processing_seconds == null
                  ? "—"
                  : `${Math.round(queue.median_processing_seconds)} sec`}
                .
              </p>
            ))}
            <h4 className="text-sm font-medium">
              Output <span className="font-normal text-muted-foreground">· last 24 UTC hours</span>
            </h4>
            <p className="text-xs text-muted-foreground">
              Recent throughput uses the last 24 UTC hour buckets, including the current partial
              hour. Verified topic decisions and publications are separate outcomes.
            </p>
            <dl className="grid grid-cols-2 gap-3">
              {[
                ["Verified topic decisions / hour", throughput.topic_decisions_per_hour ?? 0],
                ["Articles published / hour", throughput.articles_published_per_hour ?? 0],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg bg-muted/40 p-3">
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="space-y-4 min-w-0">
            <Bars
              title="Verified decisions and publications by hour"
              note="Independent-gate topic decisions, first article publications, and applied article analyses. These series overlap and must not be added. Deferred attempts and returned JSON are not decisions. UTC hours; current hour is partial."
              rows={(throughput.hours ?? []).map((hour) => ({
                ...hour,
                name: hour.hour.slice(5, 16).replace("T", " "),
              }))}
              series={[
                { key: "topic_decisions", name: "Verified topic decisions", color: colors[0] },
                { key: "articles_published", name: "Articles published", color: colors[1] },
                { key: "article_analyses", name: "Applied article analyses", color: colors[2] },
              ]}
            />
          </div>
        </div>
      )}
      {chart === "throughput" && !throughput && (
        <p className="text-sm text-muted-foreground">
          Worker capacity and throughput data unavailable.
        </p>
      )}
      {chart === "decision-efficiency" && !decisions && (
        <p className="text-sm text-muted-foreground">Topic decision metrics unavailable.</p>
      )}
      {decisions && (!chart || chart === "decision-efficiency") && (
        <div className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Selected-period topic decision rate · drain estimate applies only to actionable topics
          </p>
          <dl className="grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">Decisions per hour</dt>
              <dd className="mt-1 text-2xl font-semibold">{decisions.decisions_per_hour}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Tokens per completed bounded review</dt>
              <dd className="mt-1 text-2xl font-semibold">
                {decisions.tokens_per_completed_topic == null
                  ? "—"
                  : formatCompactCount(decisions.tokens_per_completed_topic)}
              </dd>
            </div>
            <div className="rounded-lg border p-4 sm:col-span-2">
              <dt className="text-xs text-muted-foreground">Estimated actionable topic drain</dt>
              <dd className="mt-1 text-2xl font-semibold">
                {decisions.estimated_drain_hours == null
                  ? "—"
                  : `${decisions.estimated_drain_hours} h`}
              </dd>
              <p className="text-xs text-muted-foreground">
                For {decisions.actionable ?? 0} actionable topics at this period’s rate. Excludes{" "}
                {decisions.deferred ?? 0} deferred topics, {decisions.awaiting_review ?? 0} manual
                reviews, and all article jobs.
              </p>
            </div>
          </dl>
        </div>
      )}
      <div className={chart ? "" : "grid gap-8 xl:grid-cols-2"}>
        {(!chart || chart === "inference-tokens") && (
          <Bars
            title="Recorded call tokens"
            note="By invocation start date in UTC, including failures. Cached input is a subset of input; reasoning is a subset of output. The four segments do not overlap. Missing telemetry is not zero usage."
            rows={daily}
            stacked
            series={[
              { key: "uncached", name: "Uncached input", color: colors[0] },
              { key: "cached", name: "Cached input", color: colors[1] },
              { key: "output", name: "Other output", color: colors[2] },
              { key: "reasoning", name: "Reasoning output", color: colors[3] },
            ]}
          />
        )}
        {(!chart || chart === "tokens-by-task") && (
          <Bars
            title="Tokens by task"
            horizontal
            note="Includes source relevance, article analysis, topic and relationship research, and verification. Repeated calls are counted once each, including unsuccessful work."
            rows={[...operations.values()]}
            series={tokens}
            stacked
          />
        )}
        {(!chart || chart === "tokens-by-model") && (
          <section aria-label="Tokens by model">
            <h3 className="mb-4 text-sm font-semibold">Tokens by model</h3>
            <DistributionChart
              label="Tokens by model"
              centerLabel="Tokens"
              formatValue={(value) => formatCompactCount(value).toUpperCase()}
              rows={[...models].map(([label, value], i) => ({
                label,
                value,
                color: colors[i % colors.length],
              }))}
            />
          </section>
        )}
        {(!chart || chart === "reasoning-effort") && (
          <section aria-label="Calls by reasoning effort">
            <h3 className="mb-4 text-sm font-semibold">Calls by reasoning effort</h3>
            <DistributionChart
              label="Calls by reasoning effort"
              centerLabel="Calls"
              rows={[...efforts].map(([label, value], i) => ({
                label,
                value,
                color: colors[i % colors.length],
              }))}
            />
          </section>
        )}
        {(!chart || chart === "inference-outcomes") && (
          <Bars
            title="Daily inference outcomes"
            note="Returned means the transport returned JSON, not that validation passed or a topic was approved. Running records can include interrupted calls whose completion telemetry is missing."
            rows={daily}
            stacked
            series={[
              { key: "returned", name: "Returned JSON", color: colors[0] },
              { key: "failed", name: "Failed", color: colors[2] },
              { key: "running", name: "Running / unfinished", color: colors[3] },
            ]}
          />
        )}
        {(!chart || chart === "web-searches") && (
          <Bars
            title="Daily web searches"
            note="Recorded hosted web tool operations across all inference tasks. Search operations are separate from token totals."
            rows={daily}
            series={[{ key: "searches", name: "Search operations", color: colors[1] }]}
          />
        )}
        {(!chart || chart === "topic-outcomes") && (
          <Bars
            title="Topic review outcomes"
            note="Approved and rejected are actual proposal reviews, including manual reviews. Deferred means unresolved, grouped by the latest deferral date; it is not a completed review."
            rows={topicDays}
            series={[
              { key: "approved", name: "Approved", color: colors[0] },
              { key: "rejected", name: "Rejected", color: colors[2] },
              { key: "deferred", name: "Deferred", color: colors[3] },
            ]}
          />
        )}
        {(!chart || chart === "topic-backlog") && (
          <section aria-label="Current topic backlog">
            <h3 className="mb-4 text-sm font-semibold">Current topic backlog</h3>
            <p className="mb-4 text-xs text-muted-foreground">
              Current pending topics · separated by what happens next
            </p>
            {decisions ? (
              <dl className="space-y-3">
                {[
                  {
                    label: "Actionable",
                    value: decisions.actionable ?? 0,
                    detail: "Eligible for automated processing",
                    color: colors[0],
                  },
                  {
                    label: "Deferred",
                    value: decisions.deferred ?? 0,
                    detail: "Waiting for a later attempt or prerequisites",
                    color: colors[2],
                  },
                  {
                    label: "Manual review",
                    value: decisions.awaiting_review ?? 0,
                    detail: "Needs an administrator’s decision",
                    color: colors[3],
                  },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="flex items-center justify-between gap-4 rounded-lg border p-3"
                  >
                    <div className="border-l-2 pl-3" style={{ borderColor: item.color }}>
                      <dt className="text-sm font-medium">{item.label}</dt>
                      <p className="mt-1 text-xs text-muted-foreground">{item.detail}</p>
                    </div>
                    <dd className="text-2xl font-semibold tabular-nums">
                      {item.value.toLocaleString("en")}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">Topic backlog data unavailable.</p>
            )}
            {Object.entries(decisions?.deferred_reasons ?? {}).map(([reason, count]) => (
              <p key={reason} className="mt-2 text-xs text-muted-foreground">
                {humanize(reason)}: {count}
              </p>
            ))}
          </section>
        )}
        {(!chart || chart === "repeated-stages") && (
          <Bars
            title="Repeated stages and escalations"
            note="Bounded topic workflows only. An escalation may also be a repeated stage, so these series overlap and must not be added."
            rows={topicDays}
            series={[
              { key: "repeated_stages", name: "Repeated stage calls", color: colors[2] },
              { key: "escalations", name: "Stronger model calls", color: colors[3] },
            ]}
          />
        )}
        {(!chart || chart === "inference-duration") && (
          <Bars
            title="Average inference duration"
            note="Seconds per finished transport invocation, including failed calls. This is not total topic processing time."
            rows={daily.map((day) => ({
              name: day.name,
              seconds:
                Number(day.returned) + Number(day.failed)
                  ? Number(day.duration) / (Number(day.returned) + Number(day.failed)) / 1000
                  : 0,
            }))}
            series={[{ key: "seconds", name: "Seconds", color: colors[0] }]}
          />
        )}
      </div>
    </section>
  );
}
