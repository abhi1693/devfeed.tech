/** Public chart identities also define their persistent card headings. */
export const overviewCharts = [
  {
    id: "attention",
    section: "operations",
    title: "Review and recovery",
    description:
      "Current reviews and recovery controls for items created during the selected period, plus active source failures observed in that period. Empty queues are hidden.",
  },
  {
    id: "publication-rate",
    section: "publishing",
    title: "Publication output",
    description:
      "Articles first published during the selected UTC period, including articles discovered earlier. Automatic publication excludes manual intervention; missing historical automation details leave the percentage unavailable.",
  },
  {
    id: "publication-latency",
    section: "publishing",
    title: "Time to publication",
    description:
      "Estimated discovery-to-first-publication median and 95th percentile for articles published during the selected UTC period. The full wait includes discovery on earlier dates; unavailable measurements remain gaps.",
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
      "Current health of feeds last refreshed during the selected period, including new accounts awaiting their first refresh.",
  },
  {
    id: "queue-pressure",
    section: "processing",
    title: "Queue pressure",
    description:
      "Currently queued and running jobs created during the selected period. Queue size is not a completion percentage.",
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
      "Leading sources by articles published during the selected period. An article can be attributed to more than one source.",
  },
  {
    id: "topic-coverage",
    section: "coverage",
    title: "Topic demand and coverage",
    description:
      "Active follows created during the selected period and current inferred interests from feeds computed in that period, alongside publications in the same period.",
  },
] as const;

export type ChartName = (typeof overviewCharts)[number]["id"];
export const chartDefinition = (id: ChartName) => overviewCharts.find((chart) => chart.id === id)!;
