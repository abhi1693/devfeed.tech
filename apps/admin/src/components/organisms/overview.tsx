"use client";

import { useState, type ReactNode } from "react";
import { Button } from "@/components/atoms/button";
import type { OverviewPanel as PanelData } from "@/lib/api/generated/models";
import { OverviewPanel, type PanelName } from "./overview-panel";
import { OverviewMetrics } from "./overview-metrics";
import { OverviewCharts } from "./overview-charts";
import { OverviewEngagementCharts } from "./overview-engagement-charts";
import {
  OverviewAttention,
  OverviewAudience,
  OverviewSources,
  OverviewJobReliability,
  OverviewWorkload,
  OverviewPublicationAutomation,
  OverviewBlockers,
  type OverviewSection,
} from "./overview-panels";
import { OverviewTokenChart } from "./overview-token-chart";
import { OverviewInferenceCharts } from "./overview-inference-charts";

const inference: [PanelName, string][] = [
  ["throughput", "Verified decisions and publications by hour"],
  ["decision-efficiency", "Topic decision efficiency"],
  ["inference-tokens", "Daily inference tokens"],
  ["tokens-by-task", "Tokens by task"],
  ["tokens-by-model", "Tokens by model"],
  ["reasoning-effort", "Calls by reasoning effort"],
  ["inference-outcomes", "Daily inference outcomes"],
  ["web-searches", "Daily web searches"],
  ["topic-outcomes", "Topic review outcomes"],
  ["topic-backlog", "Current topic backlog"],
  ["repeated-stages", "Repeated stages and escalations"],
  ["inference-duration", "Average inference duration"],
];

export function Overview({ initialDays = 30 }: { initialDays?: number }) {
  const [days, setDays] = useState(initialDays);
  function openSection(value: OverviewSection) {
    document
      .getElementById(
        { sources: "source-health", personalization: "personalization", operations: "attention" }[
          value
        ],
      )
      ?.scrollIntoView({ block: "start" });
  }
  function panel(
    name: PanelName,
    title: string,
    render: (data: PanelData) => ReactNode,
    compact = false,
  ) {
    return (
      <OverviewPanel
        key={name}
        panel={name}
        title={title}
        days={days}
        refresh={0}
        compact={compact}
      >
        {render}
      </OverviewPanel>
    );
  }
  function group(id: string, title: string, description: string, children: ReactNode) {
    return (
      <section
        id={id}
        aria-labelledby={`${id}-heading`}
        className="scroll-mt-6 space-y-4 border-t pt-6"
      >
        <header>
          <h2 id={`${id}-heading`} className="text-xl font-semibold tracking-tight">
            {title}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </header>
        {children}
      </section>
    );
  }
  const grid = "grid items-stretch gap-4 xl:grid-cols-2";
  function metric(name: PanelName, title: string, index: number) {
    return panel(name, title, (data) => <OverviewMetrics data={data} metric={index} />, true);
  }
  function diagnostic(name: PanelName, title: string) {
    return panel(name, title, (data) => (
      <OverviewInferenceCharts data={data.automation} chart={name} />
    ));
  }
  return (
    <section className="space-y-6" aria-label="Application overview">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Act on blockers, track publishing, and understand your audience.
          </p>
        </div>
        <div className="space-y-2">
          <div className="flex flex-wrap gap-3">
            <div
              className="flex rounded-lg border bg-muted/50 p-1"
              role="group"
              aria-label="Chart date range"
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
          </div>
        </div>
      </header>
      <nav aria-label="Overview sections" className="flex flex-wrap gap-2">
        {[
          ["attention", "Needs attention"],
          ["publishing", "Publishing"],
          ["audience", "Audience"],
          ["personalization", "Personalization"],
          ["processing", "Processing"],
          ["ai-usage", "AI usage"],
        ].map(([id, title]) => (
          <a
            key={id}
            href={`#${id}`}
            className="rounded-md border px-3 py-2 text-sm font-medium hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
          >
            {title}
          </a>
        ))}
      </nav>
      {group(
        "attention",
        "Needs attention",
        "Current blockers and waiting work. Open an item to investigate or review it.",
        <>
          {panel(
            "attention",
            "Review queue",
            (data) => (
              <OverviewAttention data={data} onOpen={openSection} />
            ),
            true,
          )}
          <div className={grid}>
            {panel("workload", "Current workload", (data) => (
              <OverviewWorkload data={data} />
            ))}
            {panel("blockers", "Publication and research blockers", (data) => (
              <OverviewBlockers data={data} />
            ))}
          </div>
        </>,
      )}
      {group(
        "publishing",
        "Publishing",
        `Publication outcomes over the last ${days} days. Today is incomplete; daily charts use UTC.`,
        <>
          <div className="grid gap-4 md:grid-cols-3">
            {metric("publications", "First publications", 0)}
            {metric("publication-time", "Median publication time", 3)}
            {panel(
              "publication-automation",
              "Published automatically",
              (data) => (
                <OverviewPublicationAutomation data={data} />
              ),
              true,
            )}
          </div>
          <div className={grid}>
            {panel("publishing", "Publishing activity", (data) => (
              <OverviewCharts data={data} chart="publishing" />
            ))}
            {panel("sources", "Articles published by source", (data) => (
              <OverviewSources data={data} />
            ))}
          </div>
        </>,
      )}
      {group(
        "audience",
        "Audience",
        "Reader interest and account adoption. Article clicks measure opens, not completed reads or returning visitors.",
        <>
          <div className={grid}>
            {metric("clicks", "Original article clicks", 1)}
            {metric("accounts", "New accounts", 2)}
          </div>
          <div className={grid}>
            {panel("readers", "Readers opening articles", (data) => (
              <OverviewEngagementCharts data={data} chart="readers" />
            ))}
            {panel("adoption", "Likes and follows", (data) => (
              <OverviewEngagementCharts data={data} chart="adoption" />
            ))}
          </div>
          <div className="space-y-4">
            <div className={grid}>
              {panel("click-depth", "Clicks per reader", (data) => (
                <OverviewEngagementCharts data={data} chart="depth" />
              ))}
              {panel("new-accounts", "Daily new accounts", (data) => (
                <OverviewEngagementCharts data={data} chart="accounts" />
              ))}
              {panel("reader-activity", "Reader activity", (data) => (
                <OverviewCharts data={data} chart="readers" />
              ))}
              {panel("popular-articles", "Reading concentration", (data) => (
                <OverviewAudience data={data} section="readers" />
              ))}
            </div>
          </div>
        </>,
      )}
      {group(
        "personalization",
        "Personalization",
        "Current feed readiness and recommendation coverage. These are snapshots, not period totals.",
        <>
          <div className={grid}>
            {panel("feed-health", "Personalized feed health", (data) => (
              <OverviewAudience data={data} section="personalization" chart="health" />
            ))}
            {panel("recommendation-reasons", "Recommendation reasons", (data) => (
              <OverviewAudience data={data} section="personalization" chart="reasons" />
            ))}
          </div>
          <div className="space-y-4">
            {panel("topic-coverage", "Interest versus coverage", (data) => (
              <OverviewAudience data={data} section="topics" />
            ))}
          </div>
        </>,
      )}
      {group(
        "processing",
        "Processing",
        "Capacity now, recent output, and the outcomes of completed work. Each view states its time window.",
        <>
          {diagnostic("throughput", "Pipeline throughput")}
          <div className={grid}>
            {diagnostic("topic-backlog", "Current topic backlog")}
            {diagnostic("decision-efficiency", "Topic decision efficiency")}
          </div>
          <div className={grid}>
            {panel("job-reliability", "Job reliability", (data) => (
              <OverviewJobReliability data={data} />
            ))}
            {diagnostic("topic-outcomes", "Topic review outcomes")}
          </div>
        </>,
      )}
      {group(
        "ai-usage",
        "AI usage",
        "Two accounting views with different coverage. Do not add these totals together or treat them as billing figures.",
        <>
          <div className={grid}>
            {panel("job-tokens", "Completed-job tokens", (data) => (
              <OverviewTokenChart data={data.automation} />
            ))}
            {diagnostic("inference-tokens", "Recorded call tokens")}
          </div>
          <div className="space-y-4">
            <div className={grid}>
              {inference
                .filter(
                  ([name]) =>
                    ![
                      "throughput",
                      "decision-efficiency",
                      "inference-tokens",
                      "topic-backlog",
                      "topic-outcomes",
                    ].includes(name),
                )
                .map(([name, title]) => diagnostic(name, title))}
            </div>
          </div>
        </>,
      )}
    </section>
  );
}
