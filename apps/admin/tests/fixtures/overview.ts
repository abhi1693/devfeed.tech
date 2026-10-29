import type { OverviewChartReport } from "@/lib/api/generated/models";
import { dateRangeBounds, defaultDateRange } from "@devfeed/ui/date-range-picker";

/** Match the public date contract while retaining each chart's typed test facts. */
export function reportForRange<T extends OverviewChartReport>(
  report: T,
  range: { from?: string | null; to?: string | null } = defaultDateRange,
): T {
  const bounds = dateRangeBounds({ from: range.from ?? undefined, to: range.to ?? undefined });
  if (!bounds) throw new Error(`Invalid test report range: ${JSON.stringify(range)}`);
  const days = (Date.parse(bounds.end) - Date.parse(bounds.start)) / 86_400_000 + 1;
  return {
    ...report,
    ...bounds,
    days,
    granularity: days <= 7 ? "hour" : days <= 90 ? "day" : days <= 366 ? "week" : "month",
  };
}

export const charts = [
  "attention",
  "publication-rate",
  "publication-latency",
  "audience-activity",
  "personalization",
  "queue-pressure",
  "throughput",
  "job-outcomes",
  "ai-activity",
  "ai-breakdown",
  "source-output",
  "topic-coverage",
] as const;
export type ChartName = (typeof charts)[number];
type ChartReports = { [K in ChartName]: Extract<OverviewChartReport, { chart: K }> };

export const emptyCharts: ChartReports = {
  attention: {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "attention",
    data: {
      reviews: [],
      blockers: [],
    },
  },
  "publication-rate": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "publication-rate",
    data: {
      summary: {},
      activity: [],
    },
  },
  "publication-latency": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "publication-latency",
    data: {
      summary: {},
      activity: [],
    },
  },
  "audience-activity": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "audience-activity",
    data: {
      activity: [],
    },
  },
  personalization: {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "personalization",
    data: {
      personalization: {},
    },
  },
  "queue-pressure": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "queue-pressure",
    data: {
      queues: [],
    },
  },
  throughput: {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "throughput",
    data: {
      throughput: [],
    },
  },
  "job-outcomes": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "job-outcomes",
    data: {
      outcomes: [],
    },
  },
  "ai-activity": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "ai-activity",
    data: {
      summary: {},
      activity: [],
    },
  },
  "ai-breakdown": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "ai-breakdown",
    data: {
      tasks: [],
      models: [],
      efforts: [],
    },
  },
  "source-output": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "source-output",
    data: {
      sources: [],
    },
  },
  "topic-coverage": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "topic-coverage",
    data: {
      topics: [],
    },
  },
};

export const populatedCharts: ChartReports = {
  attention: {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "attention",
    data: {
      full_automation: false,
      reviews: [
        {
          kind: "articles",
          count: 4,
        },
        {
          kind: "sources",
          count: 2,
        },
        {
          kind: "topics",
          count: 3,
        },
        {
          kind: "relationships",
          count: 1,
        },
      ],
      source_failures: 2,
      blockers: [
        {
          code: "analysis_failed",
          label: "Failed analysis",
          count: 2,
          action: "analyze",
          targets: [
            {
              id: "article-1",
              title: "Routing guide",
              kind: "article",
              revision: 7,
            },
          ],
        },
      ],
    },
  },
  "publication-rate": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "publication-rate",
    data: {
      summary: {
        published: 42,
        automatic_percent: 83.3,
      },
      activity: [
        {
          bucket: "2026-10-26T00:00:00Z",
          published: 42,
          automated: 35,
        },
      ],
    },
  },
  "publication-latency": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "publication-latency",
    data: {
      summary: {
        p50_seconds: 60,
        p95_seconds: 240,
      },
      activity: [
        {
          bucket: "2026-10-26T00:00:00Z",
          p50_seconds: 60,
          p95_seconds: 240,
        },
      ],
    },
  },
  "audience-activity": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "audience-activity",
    data: {
      activity: [
        {
          bucket: "2026-10-26T00:00:00Z",
          opens: 100,
          estimated_readers: 52,
          new_accounts: 4,
        },
      ],
    },
  },
  personalization: {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "personalization",
    data: {
      personalization: {
        ready: 8,
        refreshing: 2,
        expired: 1,
        pending: 1,
        not_needed: 3,
        overdue: 1,
      },
    },
  },
  "queue-pressure": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "queue-pressure",
    data: {
      queues: [
        {
          kind: "ingestion",
          queued: 4,
          running: 1,
        },
        {
          kind: "article_enrichment",
          queued: 2,
          running: 0,
        },
        {
          kind: "article_image",
          queued: 7,
          running: 2,
        },
        {
          kind: "source_enrichment",
          queued: 1,
          running: 0,
        },
        {
          kind: "analysis",
          queued: 9000,
          running: 2,
        },
        {
          kind: "topic_analysis",
          queued: 20,
          running: 1,
        },
        {
          kind: "research_verification",
          queued: 2,
          running: 0,
        },
        {
          kind: "notification",
          queued: 0,
          running: 2,
        },
      ],
    },
  },
  throughput: {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "throughput",
    data: {
      throughput: [
        {
          bucket: "2026-10-26T00:00:00Z",
          articles_published: 42,
          article_analyses: 50,
          topic_decisions: 4,
        },
      ],
    },
  },
  "job-outcomes": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "job-outcomes",
    data: {
      outcomes: [
        {
          kind: "analysis",
          completed: 50,
          failed: 2,
          p50_seconds: 10,
          p95_seconds: 25,
        },
      ],
    },
  },
  "ai-activity": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "ai-activity",
    data: {
      summary: {
        calls: 8,
        failed: 1,
        unreported: 2,
        input_tokens: 1800,
        output_tokens: 400,
        cached_input_tokens: 200,
        reasoning_tokens: 100,
        web_searches: 3,
      },
      activity: [
        {
          bucket: "2026-10-26T00:00:00Z",
          calls: 8,
          failed: 1,
          unreported: 2,
          input_tokens: 1800,
          output_tokens: 400,
          cached_input_tokens: 200,
          reasoning_tokens: 100,
          web_searches: 3,
          p50_ms: 2000,
          p95_ms: 8000,
        },
      ],
    },
  },
  "ai-breakdown": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "ai-breakdown",
    data: {
      tasks: [
        {
          name: "Article analysis",
          calls: 8,
          input_tokens: 1800,
          output_tokens: 400,
        },
      ],
      models: [
        {
          name: "Model A",
          calls: 8,
          input_tokens: 1800,
          output_tokens: 400,
        },
      ],
      efforts: [
        {
          name: "Low",
          calls: 8,
          input_tokens: 1800,
          output_tokens: 400,
        },
      ],
    },
  },
  "source-output": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "source-output",
    data: {
      sources: [
        {
          id: "source-1",
          name: "Publisher",
          publications: 42,
          consecutive_failures: 2,
        },
      ],
      source_publications_total: 60,
    },
  },
  "topic-coverage": {
    generated_at: "2026-10-26T12:00:00Z",
    days: 30,
    chart: "topic-coverage",
    data: {
      topics: [
        {
          id: "topic-1",
          name: "Python",
          followers: 8,
          inferred_users: 2,
          publications: 30,
        },
      ],
    },
  },
};
