// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { OverviewEngagementCharts } from "@/components/organisms/overview-engagement-charts";
import { populatedOverview } from "./fixtures/overview";

afterEach(cleanup);
it("shows weighted engagement, sign-ups, and distinct account adoption together", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.reader_activity = [
    { date: "2026-09-10", opens: 100, readers: 10, multi_article_readers: 5, accounts: 2 },
    { date: "2026-09-11", opens: 100, readers: 50, multi_article_readers: 10, accounts: 3 },
    { date: "2026-09-12", opens: 0, readers: 0, multi_article_readers: 0, accounts: 0 },
    { date: "2026-09-09", opens: 900, readers: null, multi_article_readers: null, accounts: 0 },
  ];
  data.insights!.adoption = {
    accounts: 100,
    liking: 20,
    following_topics: 30,
    following_sources: 10,
  };
  render(<OverviewEngagementCharts data={data} />);
  expect(screen.getByText("3.3 clicks per reader-day on average")).toBeTruthy();
  expect(screen.getByText(/25% of reader-days included multiple articles/).textContent).toContain(
    "missing history",
  );
  expect(screen.getByText("5 accounts created in this period")).toBeTruthy();
  expect(screen.getByRole("meter", { name: "Liked articles" }).getAttribute("aria-valuenow")).toBe(
    "20",
  );
  expect(screen.getByText("20 accounts · 20%")).toBeTruthy();
  expect(screen.queryByRole("tab")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "About Likes and follows" }));
  expect(screen.getByRole("tooltip").textContent).toContain("groups overlap");
});

it("shows empty states instead of meaningless charts without measured activity", () => {
  const data = structuredClone(populatedOverview);
  data.insights!.reader_activity = [
    { date: "2026-09-12", opens: null, readers: null, accounts: 0 },
  ];
  data.insights!.adoption = { accounts: 0 };
  render(<OverviewEngagementCharts data={data} />);
  expect(screen.queryByRole("figure")).toBeNull();
  expect(screen.getByText("No recorded readers in the available history.")).toBeTruthy();
  expect(screen.getByText("No accounts yet.")).toBeTruthy();
  expect(screen.queryByText(/NaN|Infinity/)).toBeNull();
});
