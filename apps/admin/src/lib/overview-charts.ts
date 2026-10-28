/** Public chart identities also define their persistent card headings. */
export const overviewCharts = [
  {
    id: "attention",
    section: "operations",
    title: "Review and recovery",
    description:
      "Current decisions and recovery controls. Empty queues are hidden; review a record or retry a failed step.",
  },
  {
    id: "publication-rate",
    section: "publishing",
    title: "Publication output",
    description:
      "First publications by UTC bucket. Automatic publication excludes manual intervention. Historical totals without automation details have no automatic percentage.",
  },
  {
    id: "publication-latency",
    section: "publishing",
    title: "Time to publication",
    description:
      "Estimated median and 95th percentile from discovery to first publication. Percentiles are merged across the selected period. Unavailable historical delay measurements remain gaps.",
  },
  {
    id: "audience-activity",
    section: "audience",
    title: "Reader activity",
    description:
      "Opens count distinct viewer/article/hour combinations. Readers are estimates; unavailable historical measurements remain gaps.",
  },
  {
    id: "personalization",
    section: "audience",
    title: "Personalized feed health",
    description:
      "Current feed readiness. These account counts are a snapshot rather than period totals.",
  },
  {
    id: "queue-pressure",
    section: "processing",
    title: "Queue pressure",
    description:
      "Current queued and running jobs across all eight job types. Queue size is not a completion percentage.",
  },
  {
    id: "throughput",
    section: "processing",
    title: "Useful output",
    description:
      "Published articles, applied article analyses, and completed topic decisions over the selected period.",
  },
  {
    id: "job-outcomes",
    section: "processing",
    title: "Job outcomes and duration",
    description:
      "Completed and failed jobs in this period. Processing durations are percentile estimates.",
  },
  {
    id: "ai-activity",
    section: "inference",
    title: "AI call activity",
    description:
      "Recorded invocation usage, not a billing estimate. Cached input and reasoning output are subsets of input and output.",
  },
  {
    id: "ai-breakdown",
    section: "inference",
    title: "Where tokens are spent",
    description:
      "The eight largest contributors plus Other. Input and output are shown separately to avoid counting subsets twice.",
  },
  {
    id: "source-output",
    section: "coverage",
    title: "Source output",
    description:
      "Leading sources by published articles. An article can be attributed to more than one source.",
  },
  {
    id: "topic-coverage",
    section: "coverage",
    title: "Topic demand and coverage",
    description:
      "The ten most followed topics with current explicit and inferred interest, alongside publications in this period.",
  },
] as const;

export type ChartName = (typeof overviewCharts)[number]["id"];
export const chartDefinition = (id: ChartName) => overviewCharts.find((chart) => chart.id === id)!;
