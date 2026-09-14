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
  const [refresh, setRefresh] = useState(0);
  function openSection(value: OverviewSection) {
    document
      .getElementById(
        { sources: "source-health", personalization: "personalization", operations: "processing" }[
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
        refresh={refresh}
        compact={compact}
      >
        {render}
      </OverviewPanel>
    );
  }
  return (
    <section className="space-y-6" aria-label="Application overview">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
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
          <Button variant="outline" onClick={() => setRefresh((value) => value + 1)}>
            Refresh all
          </Button>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {(["publications", "clicks", "accounts", "publication-time"] as const).map((name, index) =>
          panel(
            name,
            [
              "First publications",
              "Original article clicks",
              "New accounts",
              "Median publication time",
            ][index],
            (data) => <OverviewMetrics data={data} metric={index} />,
            true,
          ),
        )}
      </div>
      {panel(
        "attention",
        "Needs attention",
        (data) => (
          <OverviewAttention data={data} onOpen={openSection} />
        ),
        true,
      )}
      <div className="grid items-start gap-6 xl:grid-cols-2">
        {panel("readers", "Readers opening articles", (data) => (
          <OverviewEngagementCharts data={data} chart="readers" />
        ))}
        {panel("click-depth", "Clicks per reader", (data) => (
          <OverviewEngagementCharts data={data} chart="depth" />
        ))}
        {panel("new-accounts", "Daily new accounts", (data) => (
          <OverviewEngagementCharts data={data} chart="accounts" />
        ))}
        {panel("adoption", "Likes and follows", (data) => (
          <OverviewEngagementCharts data={data} chart="adoption" />
        ))}
        {panel("publishing", "Publishing activity", (data) => (
          <OverviewCharts data={data} chart="publishing" />
        ))}
        {panel("reader-activity", "Reader activity", (data) => (
          <OverviewCharts data={data} chart="readers" />
        ))}
        {panel("popular-articles", "Reading concentration", (data) => (
          <OverviewAudience data={data} section="readers" />
        ))}
        {panel("topic-coverage", "Interest versus coverage", (data) => (
          <OverviewAudience data={data} section="topics" />
        ))}
        {panel("feed-health", "Personalized feed health", (data) => (
          <OverviewAudience data={data} section="personalization" chart="health" />
        ))}
        {panel("recommendation-reasons", "Recommendation reasons", (data) => (
          <OverviewAudience data={data} section="personalization" chart="reasons" />
        ))}
      </div>
      {panel("sources", "Articles published by source", (data) => (
        <OverviewSources data={data} />
      ))}
      <div id="processing" className="space-y-6">
        <h2 className="text-base font-semibold">Processing health</h2>
        {panel(
          "publication-automation",
          "Published without intervention",
          (data) => (
            <OverviewPublicationAutomation data={data} />
          ),
          true,
        )}
        {panel("job-tokens", "Daily AI tokens by job type", (data) => (
          <OverviewTokenChart data={data.automation} />
        ))}
        <div className="grid items-start gap-6 xl:grid-cols-2">
          {panel("job-reliability", "Job reliability", (data) => (
            <OverviewJobReliability data={data} />
          ))}
          {panel("workload", "Current workload", (data) => (
            <OverviewWorkload data={data} />
          ))}
        </div>
        {panel("blockers", "Publication and research blockers", (data) => (
          <OverviewBlockers data={data} />
        ))}
        <div className="grid items-start gap-6 xl:grid-cols-2">
          {inference.map(([name, title]) =>
            panel(name, title, (data) => (
              <OverviewInferenceCharts data={data.automation} chart={name} />
            )),
          )}
        </div>
      </div>
    </section>
  );
}
