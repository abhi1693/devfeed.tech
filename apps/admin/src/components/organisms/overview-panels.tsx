"use client";

import Link from "next/link";
import { type ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { InfoTooltip } from "@/components/molecules/info-tooltip";
import { CoverageChart, DistributionChart, JobOutcomesChart } from "./overview-breakdown-charts";
import { DateTime } from "@/components/molecules/date-time";
import type { AdminOverview } from "@/lib/api/generated/models";
import { formatCompactCount } from "@/lib/format-count";
import { humanize } from "@/lib/resources";
import { duration } from "./overview-metrics";
import { OverviewTokenChart } from "./overview-token-chart";
import { OverviewSourceChart } from "./overview-source-chart";

const linkStyle = "font-medium text-blue-700 hover:underline dark:text-blue-400";
const number = (value: number | undefined) => (value ?? 0).toLocaleString("en");
const reasons: Record<string, string> = {
  followed_topic: "Followed topics",
  followed_source: "Followed sources",
  liked_topic: "Liked articles",
  related_topic: "Related topics",
};
const jobs: Record<string, [string, string]> = {
  ingestion: ["Feed ingestion", "/jobs/ingestion"],
  "article-enrichment": ["Article enrichment", "/jobs/enrichment/articles"],
  images: ["Image enrichment", "/jobs/enrichment/images"],
  "source-enrichment": ["Source enrichment", "/jobs/enrichment/sources"],
  analysis: ["Article analysis", "/jobs/analysis/articles"],
  "topic-analysis": ["Topic analysis", "/jobs/analysis/topics"],
  "research-verification": ["Research verification", "/queues"],
  notifications: ["Notification jobs", "/jobs/notifications"],
};

function Panel({
  title,
  description,
  children,
  id,
}: {
  title: string;
  description: string;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className="min-w-0 space-y-4 rounded-lg border bg-card p-5">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">{title}</h2>
        <InfoTooltip label={title}>{description}</InfoTooltip>
      </div>
      {children}
    </section>
  );
}

function age(value: string | null | undefined, now: string) {
  return value ? duration(Math.max(0, (Date.parse(now) - Date.parse(value)) / 1000)) : "—";
}

export function OverviewAttention({
  data,
  onOpen,
}: {
  data: AdminOverview;
  onOpen: (section: OverviewSection) => void;
}) {
  const insight = data.insights!;
  const queues = [
    {
      key: "articles",
      label: "Articles awaiting review",
      count: data.articles_pending_review,
      href: "/content/articles?review_status=pending",
    },
    {
      key: "sources",
      label: "Sources awaiting review",
      count: data.sources_pending_review,
      href: "/content/sources?approval_status=pending",
    },
    {
      key: "topics",
      label: "Topic proposals",
      count: data.topic_proposals_pending,
      href: "/taxonomy/topics?view=proposals&status=pending",
    },
    {
      key: "relationships",
      label: "Relationship proposals",
      count: data.relationship_proposals_pending,
      href: "/taxonomy/relationships/proposals?status=pending",
    },
  ];
  const failures = (insight.processing ?? []).reduce((sum, row) => sum + row.failed, 0);
  const hasAttention =
    queues.some((row) => row.count) ||
    data.sources_failing ||
    insight.personalization?.overdue ||
    failures;
  if (!hasAttention) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border bg-card px-4 py-3 text-sm">
      <span className="font-medium">Needs attention</span>
      {queues
        .filter((row) => row.count)
        .map((row) => (
          <Link
            key={row.key}
            href={row.href}
            className="text-muted-foreground hover:text-foreground hover:underline"
            title={`Oldest ${age(insight.oldest_review_at?.[row.key], data.generated_at)}`}
          >
            <strong className="mr-1 font-medium text-foreground">{number(row.count)}</strong>
            {row.label.replace("awaiting review", "to review").toLowerCase()}
          </Link>
        ))}
      {!!data.sources_failing && (
        <button
          className="text-muted-foreground hover:text-foreground hover:underline"
          onClick={() => onOpen("sources")}
        >
          <strong className="mr-1 font-medium text-foreground">
            {number(data.sources_failing)}
          </strong>
          failing {data.sources_failing === 1 ? "source" : "sources"}
        </button>
      )}
      {!!insight.personalization?.overdue && (
        <button
          className="text-muted-foreground hover:text-foreground hover:underline"
          onClick={() => onOpen("personalization")}
          title="Refreshes overdue by at least 15 minutes"
        >
          <strong className="mr-1 font-medium text-foreground">
            {number(insight.personalization.overdue)}
          </strong>
          overdue {insight.personalization.overdue === 1 ? "feed" : "feeds"}
        </button>
      )}
      {!!failures && (
        <button
          className="text-muted-foreground hover:text-foreground hover:underline"
          onClick={() => onOpen("operations")}
        >
          <strong className="mr-1 font-medium text-foreground">{number(failures)}</strong>failed{" "}
          {failures === 1 ? "job" : "jobs"}
        </button>
      )}
    </div>
  );
}

function OverviewBlockers({ data }: { data: AdminOverview }) {
  return (data.automation?.blockers ?? []).some((row) => row.count) ? (
    <div className="rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">Publication and research blockers</h3>
        <InfoTooltip label="Publication and research blockers">
          Up to five oldest records per blocker. Blockers can overlap.
        </InfoTooltip>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {data
          .automation!.blockers.filter((row) => row.count)
          .map((row) => (
            <div key={row.code} className="rounded-md border p-3">
              <p className="flex justify-between gap-3 text-sm">
                <span>{row.label}</span>
                <strong>{number(row.count)}</strong>
              </p>
              <ul className="mt-2 space-y-1">
                {row.targets.map((target) => (
                  <li key={target.id}>
                    <Link
                      className={`${linkStyle} block truncate text-xs`}
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
                  </li>
                ))}
              </ul>
            </div>
          ))}
      </div>
    </div>
  ) : null;
}

export function OverviewAudience({
  data,
  section,
}: {
  data: AdminOverview;
  section: "readers" | "topics" | "personalization";
}) {
  const insight = data.insights!;
  const personal = insight.personalization!;
  const states = ["ready", "refreshing", "expired", "pending", "not_needed"] as const;
  const labels = {
    ready: "Ready",
    refreshing: "Refreshing",
    expired: "Expired",
    pending: "Never computed",
    not_needed: "Not needed",
  };
  const colors = [
    "var(--chart-2)",
    "var(--chart-1)",
    "var(--destructive)",
    "var(--chart-3)",
    "var(--muted-foreground)",
  ];
  if (section === "readers") {
    const articles = insight.top_articles ?? [];
    const days = (insight.reader_activity ?? []).slice(-(insight.top_articles_days ?? 30));
    const total =
      days.length && days.every((day) => day.opens != null)
        ? days.reduce((sum, day) => sum + (day.opens ?? 0), 0)
        : null;
    const topFive = articles.slice(0, 5).reduce((sum, row) => sum + row.opens, 0);
    const nextFive = articles.slice(5).reduce((sum, row) => sum + row.opens, 0);
    return (
      <Panel
        title="Reading concentration"
        description={`How original article clicks are distributed across articles over the last ${insight.top_articles_days} days. This shows whether traffic is concentrated in a few articles. Likes are current totals in each article's info icon.`}
      >
        <div className="space-y-6">
          {total != null && total > 0 ? (
            <DistributionChart
              label="Reading concentration"
              centerLabel="Original article clicks"
              rows={[
                { label: "Top 5 articles", value: topFive, color: "var(--chart-1)" },
                { label: "Next 5 articles", value: nextFive, color: "var(--chart-5)" },
                {
                  label: "Other articles",
                  value: Math.max(0, total - topFive - nextFive),
                  color: "var(--chart-2)",
                },
              ]}
            />
          ) : (
            <p className="py-16 text-center text-sm text-muted-foreground">
              {total === 0
                ? "No articles have recorded opens in this period."
                : "Complete open history is unavailable for this period."}
            </p>
          )}
          <div>
            <h3 className="mb-4 text-sm font-semibold">Most opened</h3>
            <ol className="space-y-5">
              {articles.slice(0, 3).map((row, index) => (
                <li key={row.id} className="flex items-start gap-3">
                  <span className="text-sm text-muted-foreground">{index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <Link
                      className="text-sm font-medium hover:underline"
                      href={`/content/articles/${row.id}`}
                    >
                      {row.title}
                    </Link>
                    <p className="mt-1 text-xs text-muted-foreground">{number(row.opens)} opens</p>
                  </div>
                  <InfoTooltip label={row.title}>{number(row.likes)} likes now.</InfoTooltip>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </Panel>
    );
  }
  if (section === "topics")
    return (
      <Panel
        title="Interest versus coverage"
        description={`Each bubble is a topic: followers on the horizontal axis, publications in the last ${data.days} days on the vertical axis. Bubble size shows inferred users. Followed topics along the bottom have interest but no new content. Top 10 topics by follows then inferred interest; counts can overlap.`}
      >
        <p className="text-xs font-medium text-muted-foreground">Publications · {data.days}d</p>
        <CoverageChart
          label="Interest versus coverage"
          rows={(insight.coverage ?? []).map((row) => ({
            name: row.name,
            x: row.followers,
            y: row.publications,
            size: row.inferred_users,
            needsAttention: row.publications === 0,
          }))}
          xLabel="Followers now"
          yLabel="Publications"
          sizeLabel="Inferred users"
        />
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {(insight.coverage ?? [])
            .filter((row) => row.followers > 0 && !row.publications)
            .map((row) => (
              <Link
                key={row.id}
                href={`/taxonomy/topics/${row.id}`}
                className="rounded-md border border-chart-3/40 px-3 py-2 hover:bg-muted"
              >
                {row.name}
                <span className="ml-2 text-xs text-muted-foreground">No new content</span>
              </Link>
            ))}
        </div>
      </Panel>
    );
  return (
    <Panel
      id="personalization"
      title="Personalized feeds"
      description="Current stored recommendation freshness and coverage. Accounts with no inputs or results do not need analysis."
    >
      <div className="grid items-start gap-8 xl:grid-cols-2">
        <DistributionChart
          label="Personalized feed health"
          centerLabel="Accounts"
          rows={states.map((state, index) => ({
            label: labels[state],
            value: personal[state] ?? 0,
            color: colors[index],
          }))}
        />
        <div>
          <div className="mb-5 flex items-center gap-2">
            <h3 className="text-sm font-semibold">Recommendation reasons</h3>
            <InfoTooltip label="Recommendation reasons">
              Share of stored recommendations by reason. Users can have multiple reasons.{" "}
              {(personal.reasons ?? [])
                .map(
                  (row) =>
                    `${reasons[row.reason] ?? humanize(row.reason)}: ${number(row.users)} users.`,
                )
                .join(" ")}
            </InfoTooltip>
          </div>
          <DistributionChart
            label="Recommendation reasons"
            centerLabel="Recommendations"
            rows={(personal.reasons ?? []).map((row, index) => ({
              label: reasons[row.reason] ?? humanize(row.reason),
              value: row.recommendations,
              color: `var(--chart-${(index % 6) + 1})`,
            }))}
          />
        </div>
      </div>
      <div className="border-t pt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Users to inspect</h3>
            <InfoTooltip label="Users to inspect">
              {number(personal.empty_with_interests)} users have inputs but no prepared
              recommendations in a ready feed. Up to five overdue, expired, or empty feeds are
              shown.
            </InfoTooltip>
          </div>
          <Link href="/users" className={`${linkStyle} text-xs`}>
            All users <ArrowUpRight className="inline size-3" />
          </Link>
        </div>
        {personal.issues?.length ? (
          <ul className="flex flex-wrap gap-3">
            {personal.issues.map((user) => (
              <li
                key={user.id}
                className="flex items-center gap-3 rounded-md border px-3 py-2 text-sm"
              >
                <Link href={`/users/${user.id}/analysis`} className={linkStyle}>
                  {user.name}
                </Link>
                <span className="text-xs text-muted-foreground">{user.issue}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-3 text-sm text-muted-foreground">
            No expired, overdue, or empty prepared feeds to inspect.
          </p>
        )}
      </div>
    </Panel>
  );
}

export function OverviewSources({ data }: { data: AdminOverview }) {
  const insight = data.insights!;
  return (
    <Panel
      id="source-health"
      title="Articles published by source"
      description={`Share of source publications in the last ${data.days} days, across all active, approved sources. The five largest sources are shown individually; all remaining sources are grouped as Other. Counts include articles first published in this period and still published now. An article credited to multiple sources counts once per source, so the total is source publications, not unique articles.`}
    >
      <OverviewSourceChart
        rows={insight.source_performance ?? []}
        days={data.days}
        total={insight.source_publications_total ?? 0}
      />
      {!!insight.failing_sources?.length && (
        <div className="space-y-3 border-t pt-4">
          <h3 className="text-sm font-semibold">Sources with fetch errors</h3>
          <div className="flex flex-wrap gap-3">
            {insight.failing_sources.map((source) => (
              <div
                key={source.id}
                className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-chart-3/40 px-3 py-2 text-sm"
              >
                <Link
                  href={`/content/sources/${source.id}`}
                  className="min-w-0 break-words font-medium hover:underline"
                >
                  {source.name}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {source.consecutive_failures} consecutive{" "}
                  {source.consecutive_failures === 1 ? "failure" : "failures"}
                </span>
                <InfoTooltip label={source.name}>
                  Last successful fetch:{" "}
                  {source.last_success_at ? <DateTime value={source.last_success_at} /> : "Never"}.{" "}
                  {source.fetch_failures} failed fetches in this period.
                </InfoTooltip>
              </div>
            ))}
          </div>
        </div>
      )}
      <Link
        href="/content/sources"
        className={`${linkStyle} inline-flex items-center gap-1 text-sm`}
      >
        All sources <ArrowUpRight className="size-3" />
      </Link>
    </Panel>
  );
}

export function OverviewProcessing({ data }: { data: AdminOverview }) {
  const insight = data.insights!;
  return (
    <Panel
      id="processing"
      title="Processing health"
      description={`Completed and failed jobs over ${data.days} days. Queued and running counts are current. Completed notification jobs include skipped recipients, not delivery or read receipts. Job links show current status lists.`}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            Published without intervention
            <InfoTooltip label="Published without intervention">
              {number(data.automation?.published_without_intervention)} of{" "}
              {number(data.automation?.published_in_window)} first publications.
            </InfoTooltip>
          </div>
          <p className="mt-1 text-2xl font-semibold">
            {data.automation?.automatic_publication_percent == null
              ? "—"
              : `${data.automation.automatic_publication_percent}%`}
          </p>
        </div>
        <div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            Reported AI tokens
            <InfoTooltip label="Reported AI tokens">
              {number(data.automation?.analysis_tokens)} reported tokens.{" "}
              {number(data.automation?.usage_reported_runs)} runs with usage data. These are
              reported tokens, not billing figures.
            </InfoTooltip>
          </div>
          <p className="mt-1 text-2xl font-semibold">
            {formatCompactCount(data.automation?.analysis_tokens ?? 0)}
          </p>
        </div>
        <div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            Average analysis duration
            <InfoTooltip label="Average analysis duration">
              Finished article and topic jobs with duration data.
            </InfoTooltip>
          </div>
          <p className="mt-1 text-2xl font-semibold">
            {duration(insight.analysis_average_seconds)}
          </p>
        </div>
      </div>
      <OverviewTokenChart data={data.automation} />
      <div className="grid gap-8 border-t pt-5 xl:grid-cols-2">
        <div>
          <div className="mb-4 flex items-center gap-2">
            <h3 className="text-sm font-semibold">Job reliability</h3>
            <InfoTooltip label="Job reliability">
              Completed versus failed jobs for each job type in the selected period. Hover for
              absolute counts; small samples can produce extreme rates. Queued and running jobs are
              excluded.
            </InfoTooltip>
          </div>
          <JobOutcomesChart
            rows={(insight.processing ?? []).map((row) => ({
              name: jobs[row.kind]?.[0] ?? humanize(row.kind),
              completed: row.completed,
              failed: row.failed,
            }))}
          />
        </div>
        <div>
          <div className="mb-4 flex items-center gap-2">
            <h3 className="text-sm font-semibold">Current workload</h3>
            <InfoTooltip label="Current workload">
              Queued and running jobs now, across all job types. The oldest queues below identify
              where work is waiting.
            </InfoTooltip>
          </div>
          <DistributionChart
            label="Current workload"
            centerLabel="Active jobs"
            rows={[
              {
                label: "Queued",
                value: (insight.processing ?? []).reduce((sum, row) => sum + row.queued, 0),
                color: "var(--chart-3)",
              },
              {
                label: "Running",
                value: (insight.processing ?? []).reduce((sum, row) => sum + row.running, 0),
                color: "var(--chart-1)",
              },
            ]}
          />
          <ul className="mt-5 space-y-2">
            {(insight.processing ?? [])
              .filter((row) => row.queued && row.oldest_queued_at)
              .sort((a, b) => Date.parse(a.oldest_queued_at!) - Date.parse(b.oldest_queued_at!))
              .slice(0, 3)
              .map((row) => (
                <li key={row.kind} className="flex justify-between gap-3 text-sm">
                  <Link href={jobs[row.kind]?.[1] ?? "/queues"} className="hover:underline">
                    {jobs[row.kind]?.[0] ?? humanize(row.kind)}
                  </Link>
                  <span className="text-muted-foreground">
                    {row.queued} queued · oldest {age(row.oldest_queued_at, data.generated_at)}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      </div>
      <OverviewBlockers data={data} />
      <div className="flex gap-5 text-sm">
        <Link className={linkStyle} href="/workers">
          Workers
        </Link>
        <Link className={linkStyle} href="/queues">
          Queues
        </Link>
      </div>
    </Panel>
  );
}

export type OverviewSection = "sources" | "personalization" | "operations";
export function OverviewDetails({ data }: { data: AdminOverview }) {
  return (
    <div className="min-w-0 space-y-6">
      <div className="grid items-start gap-6 xl:grid-cols-2">
        <OverviewAudience data={data} section="readers" />
        <OverviewAudience data={data} section="topics" />
      </div>
      <OverviewAudience data={data} section="personalization" />
      <OverviewSources data={data} />
      <OverviewProcessing data={data} />
    </div>
  );
}
