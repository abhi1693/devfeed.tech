// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { OverviewSources } from "@/components/organisms/overview-panels";
import { populatedOverview } from "./fixtures/overview";

afterEach(cleanup);
const base = populatedOverview.insights!.source_performance![0];

it("shows only five leaders with other sources and shares of the full source total", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.source_performance = Array.from({ length: 12 }, (_, index) => ({
    ...base,
    id: String(index),
    name: `Publisher ${index}`,
    published: index + 1,
  }));
  data.insights!.source_publications_total = 100;
  data.insights!.failing_sources = [];
  render(<OverviewSources data={data} />);
  const rows = within(screen.getByRole("list", { name: "Source publication shares" })).getAllByRole(
    "listitem",
  );
  expect(rows).toHaveLength(6);
  expect(within(rows[0]).getByRole("link", { name: "Publisher 11" }).getAttribute("href")).toBe(
    "/content/sources/11",
  );
  expect(rows[0].textContent).toContain("12.0%");
  expect(rows[5].textContent).toContain("Other sources5050.0%");
  expect(screen.queryByText("Publisher 0")).toBeNull();
  expect(screen.getByRole("figure", { name: "Articles published by source" })).toBeTruthy();
  expect(screen.queryByRole("tab")).toBeNull();
});

it("keeps fetch failures visible when there are no publications", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.source_performance = [{ ...base, published: 0 }];
  data.insights!.source_publications_total = 0;
  data.insights!.failing_sources = [{ ...base, published: 0, consecutive_failures: 1 }];
  render(<OverviewSources data={data} />);
  expect(
    screen.getByText("No articles were published from active sources in this period."),
  ).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Sources with fetch errors" })).toBeTruthy();
  expect(screen.getByText("1 consecutive failure")).toBeTruthy();
  expect(screen.queryByRole("figure")).toBeNull();
});

it("shows every job type in workload, including empty types, without double counting", async () => {
  const { OverviewWorkload } = await import("@/components/organisms/overview-panels");
  const data = structuredClone(populatedOverview);
  data.insights!.processing = [
    { kind: "analysis", queued: 7, running: 2, completed: 0, failed: 0, oldest_queued_at: null },
  ];
  render(<OverviewWorkload data={data} />);
  const rows = within(
    screen.getByRole("table", { name: "Workload counts by job type" }),
  ).getAllByRole("link");
  expect(rows).toHaveLength(8);
  expect(screen.getByText("7", { selector: "dd" })).toBeTruthy();
  expect(screen.getByText("2", { selector: "dd" })).toBeTruthy();
  expect(
    within(screen.getByRole("table", { name: "Workload counts by job type" })).getAllByRole("link"),
  ).toHaveLength(8);
  expect(screen.getByRole("figure", { name: "Running jobs by type" })).toBeTruthy();
});

it("keeps stable lane positions as queue sizes change", async () => {
  const { OverviewWorkload } = await import("@/components/organisms/overview-panels");
  const data = structuredClone(populatedOverview);
  data.insights!.processing = [
    { kind: "ingestion", queued: 1, running: 1, completed: 0, failed: 0, oldest_queued_at: null },
    { kind: "analysis", queued: 9000, running: 2, completed: 0, failed: 0, oldest_queued_at: null },
  ];
  render(<OverviewWorkload data={data} />);
  const links = within(
    screen.getByRole("table", { name: "Workload counts by job type" }),
  ).getAllByRole("link");
  expect(links[4].getAttribute("href")).toBe("/jobs/analysis/articles");
  expect(links[0].getAttribute("href")).toBe("/jobs/ingestion");
  expect(screen.getByText("9,001", { selector: "dd" })).toBeTruthy();
  expect(screen.getByText("3", { selector: "dd" })).toBeTruthy();
});

it("shows an explicit clear state when all eight queues are idle", async () => {
  const { OverviewWorkload } = await import("@/components/organisms/overview-panels");
  const data = structuredClone(populatedOverview);
  data.insights!.processing = [];
  render(<OverviewWorkload data={data} />);
  expect(screen.getByText("All queues are clear. No jobs waiting or running.")).toBeTruthy();
  expect(screen.queryByRole("list", { name: "Active queues" })).toBeNull();
  expect(
    within(screen.getByRole("table", { name: "Workload counts by job type" })).getAllByRole("link"),
  ).toHaveLength(8);
});

it("shows absolute job outcomes with failing job types first", async () => {
  const { OverviewJobReliability } = await import("@/components/organisms/overview-panels");
  const data = structuredClone(populatedOverview);
  data.insights!.processing = [
    {
      kind: "ingestion",
      queued: 9000,
      running: 20,
      completed: 800,
      failed: 0,
      oldest_queued_at: null,
    },
    { kind: "analysis", queued: 7, running: 2, completed: 30, failed: 5, oldest_queued_at: null },
  ];
  render(<OverviewJobReliability data={data} />);
  expect(screen.getByText("830", { selector: "dd" })).toBeTruthy();
  expect(screen.getByText("5", { selector: "dd" })).toBeTruthy();
  const rows = within(screen.getByRole("table", { name: "Finished jobs by type" })).getAllByRole(
    "row",
  );
  expect(rows[1].textContent).toContain("Article analysis305");
  expect(rows[2].textContent).toContain("Feed ingestion8000");
});
