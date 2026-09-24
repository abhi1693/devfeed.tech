// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { renderAdmin } from "./render-admin";
import { OverviewInferenceCharts } from "@/components/organisms/overview-inference-charts";
import type { AutomationOverview } from "@/lib/api/generated/models";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("explains absent per-call telemetry without fabricating history or costs", () => {
  renderAdmin(<OverviewInferenceCharts />);
  expect(screen.getByText(/starts with v0.0.9/)).toBeTruthy();
  expect(screen.getByText(/not API bills or weekly quota percentages/)).toBeTruthy();
  expect(screen.getAllByText("No recorded data in this period.").length).toBeGreaterThan(0);
});

it("shows new and v0.0.9 series with deferred topics separate from completed reviews", () => {
  const data: AutomationOverview = {
    blockers: [],
    published_in_window: 0,
    published_without_intervention: 0,
    automatic_publication_percent: null,
    median_ingestion_to_publication_seconds: null,
    analysis_tokens: 120,
    analysis_duration_ms: 1000,
    usage_reported_runs: 1,
    topic_decisions: {
      pending: 12,
      actionable: 7,
      deferred: 4,
      awaiting_review: 1,
      decisions_per_hour: 3,
      estimated_drain_hours: 2.3,
      tokens_per_completed_topic: 120,
      deferred_reasons: { token_budget_exhausted: 4 },
    },
    inference: {
      first_recorded_at: "2026-09-13T00:00:00Z",
      topic_activity: [
        {
          date: "2026-09-13",
          approved: 2,
          rejected: 1,
          deferred: 4,
          calls: 5,
          escalations: 1,
          repeated_stages: 2,
        },
      ],
      activity: [
        {
          date: "2026-09-13",
          operation: "source_relevance",
          model: "gpt-6-luna",
          effort: "low",
          calls: 1,
          returned: 1,
          failed: 0,
          running: 0,
          unreported: 0,
          input_tokens: 100,
          cached_input_tokens: 80,
          output_tokens: 20,
          reasoning_tokens: 10,
          web_searches: 2,
          duration_ms: 1000,
        },
      ],
    },
  };
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    new DOMRect(0, 0, 600, 400),
  );
  data.throughput = {
    capacity_observed: true,
    topic_workers: 3,
    idle_topic_workers: 1,
    article_workers: 1,
    idle_article_workers: 0,
    shared_workers: 0,
    topic_admission_limit: 6,
    topic_decisions_per_hour: 12,
    articles_published_per_hour: 20,
    hours: [
      {
        hour: "2026-09-13T12:00:00Z",
        topic_decisions: 12,
        articles_published: 20,
        article_analyses: 25,
      },
    ],
    queues: [{ kind: "topic", queued: 3, running: 3, oldest_due_seconds: 120 }],
  };
  const operations = [
    "article_analysis",
    "relationship_research",
    "source_relevance",
    "topic_discovery",
    "topic_draft",
    "topic_research",
    "topic_verification",
  ];
  const sample = data.inference!.activity![0];
  data.inference!.activity = operations.map((operation) => ({ ...sample, operation }));
  renderAdmin(<OverviewInferenceCharts data={data} />);
  const chart = screen.getByRole("figure", { name: "Tokens by task" });
  const labels = Array.from(
    chart.querySelectorAll(".recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value"),
  );
  expect(labels).toHaveLength(operations.length);
  operations.forEach((operation, index) => {
    expect(labels[index].textContent?.toLowerCase().replace(/\s/g, "")).toBe(
      operation.replaceAll("_", ""),
    );
  });
  for (const title of [
    "Verified decisions and publications by hour",
    "Recorded call tokens",
    "Tokens by task",
    "Tokens by model",
    "Calls by reasoning effort",
    "Daily inference outcomes",
    "Daily web searches",
    "Topic review outcomes",
    "Current topic backlog",
    "Repeated stages and escalations",
    "Average inference duration",
  ]) {
    expect(screen.getByRole("heading", { name: title })).toBeTruthy();
  }
  expect(screen.getByRole("table", { name: "Worker capacity" })).toBeTruthy();
  expect(screen.getByText(/oldest due 2 min/)).toBeTruthy();
  expect(screen.getByText("2.3 h")).toBeTruthy();
  expect(screen.getByText(/Token Budget Exhausted: 4/i)).toBeTruthy();
  expect(screen.getByText("Returned JSON")).toBeTruthy();
});
