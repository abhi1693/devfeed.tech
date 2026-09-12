// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { OverviewSources } from "@/components/organisms/overview-panels";
import { populatedOverview } from "./fixtures/overview";

afterEach(cleanup);
const base = populatedOverview.insights!.source_performance![0];

it("ranks publications with visible source names and counts, independently of discovery volume", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.source_performance = [
    { ...base, id: "low", name: "High-volume source", discovered: 500, published: 3 },
    { ...base, id: "zero", name: "Unpublished source", discovered: 1000, published: 0 },
    { ...base, id: "high", name: "Productive source", discovered: 2, published: 17 },
  ];
  data.insights!.failing_sources = [];
  render(<OverviewSources data={data} />);
  const rows = within(
    screen.getByRole("list", { name: "Sources ranked by published articles" }),
  ).getAllByRole("listitem");
  expect(rows).toHaveLength(2);
  expect(
    within(rows[0]).getByRole("link", { name: "Productive source" }).getAttribute("href"),
  ).toBe("/content/sources/high");
  expect(rows[0].textContent).toContain("17 articles published");
  expect(rows[1].textContent).toContain("3 articles published");
  expect(screen.queryByText("Unpublished source")).toBeNull();
  expect(screen.queryByText(/2 articles discovered/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "About Productive source output" }));
  expect(screen.getByRole("tooltip").textContent).toContain("2 articles discovered");
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByRole("table")).toBeNull();
});

it("keeps fetch failures visible when there are no publications", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.source_performance = [{ ...base, published: 0 }];
  data.insights!.failing_sources = [{ ...base, published: 0, consecutive_failures: 1 }];
  render(<OverviewSources data={data} />);
  expect(
    screen.getByText("No articles were published from active sources in this period."),
  ).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Sources with fetch errors" })).toBeTruthy();
  expect(screen.getByText("1 consecutive failure")).toBeTruthy();
  expect(screen.queryByRole("figure")).toBeNull();
});
