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
  const rows = screen.getAllByRole("listitem");
  expect(rows).toHaveLength(8);
  expect(screen.getByText("7 queued · 2 running")).toBeTruthy();
  expect(screen.getAllByText("0 queued · 0 running")).toHaveLength(7);
  expect(screen.getByText("9", { selector: "strong.text-3xl" })).toBeTruthy();
});
