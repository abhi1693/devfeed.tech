// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { OverviewCharts } from "@/components/organisms/overview-charts";
import { populatedOverview } from "./fixtures/overview";

afterEach(cleanup);

it("shows each content type once in alphabetical order and can return to publication totals", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.reader_activity = [
    {
      date: "2026-09-08",
      added: 8,
      published: 6,
      content_types: { tutorial: 3, news: 2, article: 1 },
    },
    {
      date: "2026-09-09",
      added: 4,
      published: 2,
      content_types: { news: 1, tutorial: 1 },
    },
  ];
  render(<OverviewCharts data={data} chart="publishing" />);
  expect(screen.getByText("First published")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Content types" }));
  const legend = screen.getByText("Article").parentElement!;
  expect(Array.from(legend.children, (item) => item.textContent)).toEqual([
    "Article",
    "News",
    "Tutorial",
  ]);
  expect(screen.getAllByText("News")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Content types" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  fireEvent.click(screen.getByRole("button", { name: "Totals" }));
  expect(screen.getByText("First published")).toBeTruthy();
  expect(screen.queryByText("Tutorial")).toBeNull();
});
