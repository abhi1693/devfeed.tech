// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { OverviewChartContent } from "@/components/organisms/overview-chart-content";
import { ChartPlaceholder } from "@/components/molecules/overview-chart-placeholder";
import { adminAutomationRecover } from "@/lib/api/generated/admin";
import { notify, notifyFailure } from "@/lib/notifications";
import { populatedCharts, emptyCharts, charts } from "./fixtures/overview";
import { renderAdmin } from "./render-admin";

vi.mock("@/lib/api/generated/admin", () => ({ adminAutomationRecover: vi.fn() }));
vi.mock("@/lib/notifications", () => ({
  notify: { success: vi.fn(), warning: vi.fn() },
  notifyFailure: vi.fn(),
}));
vi.mock("recharts", () => {
  const container = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  const chart = ({ children, data }: { children?: ReactNode; data?: object[] }) => (
    <div data-testid="chart" data-rows={JSON.stringify(data)}>
      {children}
    </div>
  );
  const series = ({ name, dataKey }: { name?: string; dataKey?: string }) => (
    <span data-series={dataKey}>{name}</span>
  );
  return {
    ResponsiveContainer: container,
    AreaChart: chart,
    LineChart: chart,
    BarChart: chart,
    PieChart: container,
    Area: series,
    Line: series,
    Bar: series,
    Pie: chart,
    Cell: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
    Legend: () => null,
    XAxis: () => null,
    YAxis: () => null,
  };
});
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("shows twelve purposeful cards from independently typed chart documents", () => {
  const { container } = renderAdmin(
    <>
      {charts.map((chart) => (
        <OverviewChartContent key={chart} report={populatedCharts[chart]} />
      ))}
    </>,
  );
  expect(container.querySelectorAll("[data-overview-card]")).toHaveLength(12);
  expect(screen.getByRole("heading", { name: "Publication output" })).toBeTruthy();
  expect(screen.getByText("83.3%")).toBeTruthy();
  expect(screen.getByRole("link", { name: "2 source failures" })).toBeTruthy();
  expect(screen.getByRole("table", { name: "Job outcomes and duration" }).textContent).toContain(
    "50",
  );
  expect(screen.getByRole("table", { name: "Topic demand and coverage" }).textContent).toContain(
    "Python",
  );
  expect(screen.queryByText(/Updated |Checked \d+s|since last check/)).toBeNull();
});

it("changes audience and AI questions locally while keeping subset tokens separate", () => {
  const choose = (label: string, option: string) => {
    fireEvent.click(screen.getByRole("combobox", { name: label }));
    fireEvent.click(screen.getByRole("option", { name: option }));
  };
  const { container } = renderAdmin(
    <>
      <OverviewChartContent report={populatedCharts["audience-activity"]} />
      <OverviewChartContent report={populatedCharts["personalization"]} />
      <OverviewChartContent report={populatedCharts["ai-activity"]} />
      <OverviewChartContent report={populatedCharts["ai-breakdown"]} />
    </>,
  );
  const audience = screen.getByRole("figure", { name: "Audience activity chart" });
  expect(audience.querySelector("[data-series=opens]")).toBeTruthy();
  choose("Audience metric", "Estimated readers");
  expect(audience.querySelector("[data-series=estimated_readers]")).toBeTruthy();
  choose("Audience metric", "New accounts");
  expect(audience.querySelector("[data-series=new_accounts]")).toBeTruthy();
  const ai = screen.getByRole("figure", { name: "AI activity chart" });
  expect(ai.querySelectorAll("[data-series]")).toHaveLength(2);
  expect(ai.querySelector("[data-series=cached_input_tokens]")).toBeNull();
  expect(ai.querySelector("[data-series=reasoning_tokens]")).toBeNull();
  for (const [metric, key] of [
    ["Calls and failures", "failed"],
    ["Web searches", "web_searches"],
    ["Estimated duration", "p95_ms"],
  ]) {
    choose("AI activity metric", metric);
    expect(ai.querySelector(`[data-series=${key}]`)).toBeTruthy();
  }
  choose("AI usage breakdown", "By model");
  expect(
    screen
      .getByRole("figure", { name: "AI token breakdown chart" })
      .querySelector("[data-rows]")
      ?.getAttribute("data-rows"),
  ).toContain("Model A");
  choose("AI usage breakdown", "By reasoning effort");
  expect(container.textContent).toContain("without usage");
  expect(adminAutomationRecover).not.toHaveBeenCalled();
});

it("keeps unknown archived automation and latency unavailable", () => {
  const rate = structuredClone(populatedCharts["publication-rate"]);
  rate.data.summary = { published: 9, automatic_percent: null };
  const delay = structuredClone(populatedCharts["publication-latency"]);
  delay.data.summary = { p50_seconds: null, p95_seconds: null };
  renderAdmin(
    <>
      <OverviewChartContent report={rate} />
      <OverviewChartContent report={delay} />
    </>,
  );
  expect(screen.queryByText("0.0%")).toBeNull();
  expect(screen.getAllByText("—")).toHaveLength(3);
  expect(screen.getByText("9")).toBeTruthy();
});

it("preserves a chart's selected question while a loading plot hides old values", () => {
  const chart = (loading: boolean) => (
    <ChartPlaceholder value={loading ? <div role="status">Loading plot</div> : null}>
      <OverviewChartContent report={populatedCharts["audience-activity"]} />
    </ChartPlaceholder>
  );
  const view = renderAdmin(chart(false));
  fireEvent.click(screen.getByRole("combobox", { name: "Audience metric" }));
  fireEvent.click(screen.getByRole("option", { name: "New accounts" }));
  view.rerender(chart(true));
  expect(screen.getByRole("heading", { name: "Reader activity" })).toBeTruthy();
  expect(screen.getByText("Loading plot")).toBeTruthy();
  expect(screen.queryByRole("figure", { name: "Audience activity chart" })).toBeNull();
  view.rerender(chart(false));
  expect(screen.getByRole("combobox", { name: "Audience metric" }).textContent).toContain(
    "New accounts",
  );
  expect(
    screen
      .getByRole("figure", { name: "Audience activity chart" })
      .querySelector("[data-series=new_accounts]"),
  ).toBeTruthy();
});

it("shows source Other without losing the per-source failure meaning", () => {
  renderAdmin(
    <>
      {" "}
      <OverviewChartContent report={populatedCharts["source-output"]} />
      <OverviewChartContent report={populatedCharts["topic-coverage"]} />{" "}
    </>,
  );
  const bars = screen.getByRole("figure", { name: "Publications by source" });
  const rows = JSON.parse(bars.querySelector("[data-rows]")!.getAttribute("data-rows")!);
  expect(rows.at(-1)).toEqual({ name: "Other", publications: 18 });
  expect(screen.getByText("Publisher: 2 consecutive fetch failures")).toBeTruthy();
});

it("links all eight queue kinds to their actual recovery destinations", () => {
  renderAdmin(
    <>
      {" "}
      <OverviewChartContent report={populatedCharts["queue-pressure"]} />
      <OverviewChartContent report={populatedCharts["throughput"]} />
      <OverviewChartContent report={populatedCharts["job-outcomes"]} />{" "}
    </>,
  );
  const queues = screen.getByRole("table", { name: "Workload counts by job type" });
  expect(within(queues).getAllByRole("link")).toHaveLength(8);
  expect(within(queues).getByRole("link", { name: "Article image" }).getAttribute("href")).toBe(
    "/jobs/enrichment/images",
  );
  expect(within(queues).getByRole("link", { name: "Notification" }).getAttribute("href")).toBe(
    "/jobs/notifications",
  );
  expect(screen.getByText("9,036")).toBeTruthy();
});

it.each(["queued", "published", "rejected", "would_publish"] as const)(
  "refreshes only after successful %s recovery with revision and CSRF",
  async (status) => {
    vi.mocked(adminAutomationRecover).mockResolvedValue({ status });
    const changed = vi.fn();
    renderAdmin(<OverviewChartContent report={populatedCharts["attention"]} onChange={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "Analyze again" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(adminAutomationRecover).toHaveBeenCalledExactlyOnceWith(
      "article-1",
      "analyze",
      { expected_revision: 7 },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    );
    expect(notify.success).toHaveBeenCalledOnce();
  },
);

it("retains recovery controls after a revision failure without falsely refreshing", async () => {
  const failure = new Error("Stale revision");
  vi.mocked(adminAutomationRecover).mockRejectedValue(failure);
  const changed = vi.fn();
  renderAdmin(<OverviewChartContent report={populatedCharts["attention"]} onChange={changed} />);
  const button = screen.getByRole("button", { name: "Analyze again" });
  fireEvent.click(button);
  await waitFor(() =>
    expect(notifyFailure).toHaveBeenCalledWith(failure, "Could not complete recovery"),
  );
  expect(changed).not.toHaveBeenCalled();
  expect((button as HTMLButtonElement).disabled).toBe(false);
});

it("leaves record navigation available when full automation owns recovery", () => {
  const data = structuredClone(populatedCharts.attention);
  data.data.full_automation = true;
  renderAdmin(<OverviewChartContent report={data} />);
  expect(screen.queryByRole("button", { name: "Analyze again" })).toBeNull();
  expect(screen.getByRole("link", { name: "Routing guide" }).getAttribute("href")).toBe(
    "/content/articles/article-1",
  );
});

it("renders empty sections without inventing activity or blockers", () => {
  const { container } = renderAdmin(
    <>
      {charts.map((chart) => (
        <OverviewChartContent key={chart} report={emptyCharts[chart]} />
      ))}
    </>,
  );
  expect(container.querySelectorAll("[data-overview-card]")).toHaveLength(12);
  expect(screen.getByText("All clear")).toBeTruthy();
  expect(screen.getAllByText("No activity").length).toBeGreaterThan(4);
  expect(screen.queryByRole("button", { name: "Analyze again" })).toBeNull();
});

it("moves metric explanations into accessible tooltips", async () => {
  renderAdmin(
    <>
      {" "}
      <OverviewChartContent report={populatedCharts["publication-rate"]} />
      <OverviewChartContent report={populatedCharts["publication-latency"]} />{" "}
    </>,
  );
  expect(screen.queryByText(/Articles first published during the selected UTC period/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "About Publication output" }));
  expect(await screen.findByRole("tooltip")).toBeTruthy();
  expect(
    screen.getAllByText(/Articles first published during the selected UTC period/).length,
  ).toBeGreaterThan(0);
});

it("omits empty review queues while keeping positive review decisions visible", () => {
  const data = structuredClone(populatedCharts.attention);
  data.data.reviews = [
    { kind: "articles", count: 0 },
    { kind: "sources", count: 2 },
  ];
  renderAdmin(<OverviewChartContent report={data} />);
  expect(screen.queryByRole("link", { name: /Articles/ })).toBeNull();
  expect(screen.getByRole("link", { name: /2 Sources/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Analyze again" })).toBeTruthy();
});

it("retains full publication waits for older articles published within a one-day interval", () => {
  const delay = structuredClone(populatedCharts["publication-latency"]);
  delay.days = 1;
  delay.start = "2026-10-29";
  delay.end = "2026-10-29";
  delay.data.summary = { p50_seconds: 4.1 * 3600, p95_seconds: 172.1 * 3600 };
  renderAdmin(<OverviewChartContent report={delay} />);
  expect(screen.getByText("4.1 hr")).toBeTruthy();
  expect(screen.getByText("172.1 hr")).toBeTruthy();
});
