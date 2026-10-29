// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { dateRangeBounds, defaultDateRange, type DateRange } from "@devfeed/ui/date-range-picker";
import { RefreshSettings, saveRefresh } from "./refresh-settings";
import { Overview } from "@/components/organisms/overview";
import { OverviewChart } from "@/components/organisms/overview-chart";
import { ChartCard } from "@/components/molecules/overview-chart";
import { chartDefinition } from "@/lib/overview-charts";
import type { OverviewChartReport } from "@/lib/api/generated/models";
import { adminOverviewChart } from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import { populatedCharts, charts, reportForRange } from "./fixtures/overview";

vi.mock("@/lib/api/generated/admin", () => ({ adminOverviewChart: vi.fn() }));
vi.mock("@/components/organisms/overview-chart-content", () => ({
  OverviewChartContent: ({ report }: { report: OverviewChartReport }) => (
    <ChartCard {...chartDefinition(report.chart)}>
      <div data-report-start={report.start} data-report-end={report.end}>
        {report.chart} ready
      </div>
    </ChartCard>
  ),
}));
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
function showOverview(initialRange?: DateRange) {
  return render(
    <RefreshSettings>
      <Overview initialRange={initialRange} />
    </RefreshSettings>,
  );
}
function publication(range: DateRange = { from: "now-29d/d", to: "now" }) {
  return (
    <RefreshSettings>
      <OverviewChart chart="publication-rate" range={range} />
    </RefreshSettings>
  );
}
async function chooseRange(label: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Chart date range" }));
  await user.click(screen.getByRole("button", { name: label }));
}
const publicationCard = () =>
  document.querySelector('[data-overview-card="publication-rate"]') as HTMLElement;

it("fetches each of twelve chart documents independently", async () => {
  vi.mocked(adminOverviewChart).mockImplementation(async (chart) => populatedCharts[chart]);
  showOverview();
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(12));
  expect(
    vi
      .mocked(adminOverviewChart)
      .mock.calls.map(([chart]) => chart)
      .sort(),
  ).toEqual([...charts].sort());
  for (const chart of charts) expect(await screen.findByText(`${chart} ready`)).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Overview sections" })).toBeNull();
});

it("refreshes only Queue pressure after charts load, including focus and interval changes", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart).mockImplementation(async (chart) => populatedCharts[chart]);
  showOverview();
  await act(async () => {});
  expect(adminOverviewChart).toHaveBeenCalledTimes(12);
  const expectCounts = (queue: number) => {
    for (const chart of charts)
      expect(
        vi.mocked(adminOverviewChart).mock.calls.filter(([name]) => name === chart),
        chart,
      ).toHaveLength(chart === "queue-pressure" ? queue : 1);
  };
  await saveRefresh(5);
  await act(() => vi.advanceTimersByTimeAsync(15_000));
  expectCounts(4);
  await act(async () => fireEvent(window, new Event("blur")));
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expectCounts(4);
  await act(async () => fireEvent(window, new Event("focus")));
  expectCounts(5);
  await saveRefresh(60);
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expectCounts(6);
  await saveRefresh(0);
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expectCounts(6);
});

it("places a loader inside a slow chart while its neighboring chart is usable", async () => {
  let finish!: (report: (typeof populatedCharts)["publication-rate"]) => void;
  vi.mocked(adminOverviewChart).mockImplementation((chart) =>
    chart === "publication-rate"
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : Promise.resolve(populatedCharts[chart]),
  );
  showOverview();
  expect(await screen.findByText("publication-latency ready")).toBeTruthy();
  const card = within(publicationCard());
  expect(card.getByRole("heading", { name: "Publication output" })).toBeTruthy();
  expect(card.getByRole("status", { name: "Loading Publication output" })).toBeTruthy();
  expect(card.getAllByText("—")).toHaveLength(2);
  expect(document.querySelectorAll("[data-chart-skeleton]")).toHaveLength(0);
  await act(async () => finish(populatedCharts["publication-rate"]));
  expect(await screen.findByText("publication-rate ready")).toBeTruthy();
  expect(adminOverviewChart).toHaveBeenCalledTimes(12);
});

it("retries one failed chart without reloading the other chart in its section", async () => {
  vi.mocked(adminOverviewChart).mockImplementation(async (chart) => {
    if (chart === "publication-rate") throw new ApiError(503);
    return populatedCharts[chart];
  });
  showOverview();
  const retry = await within(publicationCard()).findByRole("button", { name: "Try again" });
  expect(await screen.findByText("publication-latency ready")).toBeTruthy();
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(12));
  vi.mocked(adminOverviewChart).mockResolvedValue(populatedCharts["publication-rate"]);
  fireEvent.click(retry);
  expect(await screen.findByText("publication-rate ready")).toBeTruthy();
  expect(adminOverviewChart).toHaveBeenCalledTimes(13);
  expect(vi.mocked(adminOverviewChart).mock.calls[12][0]).toBe("publication-rate");
});

it("cancels old range requests and ignores their late responses", async () => {
  const previous = dateRangeBounds(defaultDateRange)!;
  const next = dateRangeBounds({ from: "now-6d/d", to: "now" })!;
  const old: {
    resolve: (value: OverviewChartReport) => void;
    signal: AbortSignal;
    chart: keyof typeof populatedCharts;
  }[] = [];
  vi.mocked(adminOverviewChart).mockImplementation((chart, params, options) =>
    params?.from === previous.start && params.to === previous.end
      ? new Promise((resolve) =>
          old.push({ resolve, signal: options!.signal as AbortSignal, chart }),
        )
      : Promise.resolve(reportForRange(populatedCharts[chart], { from: "now-6d/d", to: "now" })),
  );
  showOverview();
  await waitFor(() => expect(old).toHaveLength(4));
  await chooseRange("Last 7 days");
  expect(old.every(({ signal }) => signal.aborted)).toBe(true);
  await act(async () => old.forEach(({ resolve, chart }) => resolve(populatedCharts[chart])));
  await waitFor(() =>
    expect(
      vi
        .mocked(adminOverviewChart)
        .mock.calls.filter(([, params]) => params?.from === next.start && params.to === next.end),
    ).toHaveLength(12),
  );
  expect(
    vi.mocked(adminOverviewChart).mock.calls.filter(([chart]) => chart === "attention"),
  ).toHaveLength(2);
  expect(await screen.findByText("publication-rate ready")).toBeTruthy();
});

it("changes every chart's month and calendar-year window, including attention", async () => {
  vi.mocked(adminOverviewChart).mockImplementation(async (chart, params) =>
    reportForRange(populatedCharts[chart], params),
  );
  showOverview();
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(12));
  await chooseRange("Last 3 months");
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(24));
  expect(new URL(location.href).searchParams.get("from")).toBe("now-3M");
  expect(new URL(location.href).searchParams.get("to")).toBe("now");
  const months = dateRangeBounds({ from: "now-3M", to: "now" })!;
  expect(
    vi
      .mocked(adminOverviewChart)
      .mock.calls.filter(([, params]) => params?.from === months.start && params.to === months.end),
  ).toHaveLength(12);
  await chooseRange("Last year");
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(36));
  expect(new URL(location.href).searchParams.get("from")).toBe("now-1y/y");
  expect(new URL(location.href).searchParams.get("to")).toBe("now-1y/y");
  expect(
    vi.mocked(adminOverviewChart).mock.calls.filter(([chart]) => chart === "attention"),
  ).toHaveLength(3);
});

it("uses complete historical window identities even when their lengths match", async () => {
  vi.mocked(adminOverviewChart).mockImplementation(async (chart, params) =>
    reportForRange(populatedCharts[chart], params),
  );
  const first = { from: "2024-02-01", to: "2024-02-07" };
  const second = { from: "2024-02-08", to: "2024-02-14" };
  const view = render(publication(first));
  await screen.findByText("publication-rate ready");
  view.rerender(publication(second));
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(2));
  expect(vi.mocked(adminOverviewChart).mock.calls.map(([, params]) => params)).toEqual([
    first,
    second,
  ]);
});

it("replaces the old range's plot and metrics with an in-card loader", async () => {
  let finish!: (report: (typeof populatedCharts)["publication-rate"]) => void;
  vi.mocked(adminOverviewChart)
    .mockResolvedValueOnce(populatedCharts["publication-rate"])
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const view = render(publication());
  await screen.findByText("publication-rate ready");
  view.rerender(publication({ from: "now-6d/d", to: "now" }));
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("publication-rate ready")).toBeNull();
  expect(
    within(publicationCard()).getByRole("status", { name: "Loading Publication output" }),
  ).toBeTruthy();
  expect(within(publicationCard()).getAllByText("—")).toHaveLength(2);
  expect(screen.queryByText(/Loading range|previous range/)).toBeNull();
  expect(document.querySelector("[data-overview-chart]")?.getAttribute("aria-busy")).toBe("true");
  await act(async () => finish(populatedCharts["publication-rate"]));
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
});

it("keeps preparation inside the chart and checks completion after five seconds", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart)
    .mockRejectedValueOnce(new ApiError(503, undefined, {}, "analytics_preparing"))
    .mockResolvedValue(populatedCharts["publication-rate"]);
  render(publication());
  await act(async () => {});
  expect(
    within(publicationCard()).getByRole("status", { name: "Loading Publication output" }),
  ).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(4999));
  expect(adminOverviewChart).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
});

it("keeps checking delayed preparation automatically even when refresh is paused", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart).mockRejectedValue(
    new ApiError(503, undefined, {}, "analytics_preparing"),
  );
  render(publication());
  await act(async () => {});
  await saveRefresh(0);
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(3);
  expect(within(publicationCard()).getByText("Still preparing this chart")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(14_999));
  expect(adminOverviewChart).toHaveBeenCalledTimes(3);
  vi.mocked(adminOverviewChart).mockResolvedValue(populatedCharts["publication-rate"]);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(adminOverviewChart).toHaveBeenCalledTimes(4);
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(4);
});

it("pauses preparing checks while hidden and checks immediately when the page returns", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  try {
    vi.mocked(adminOverviewChart).mockRejectedValue(
      new ApiError(503, undefined, {}, "analytics_preparing"),
    );
    render(publication());
    await act(async () => {});
    await saveRefresh(0);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(within(publicationCard()).getByText("Still preparing this chart")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(adminOverviewChart).toHaveBeenCalledTimes(1);
    vi.mocked(adminOverviewChart).mockResolvedValue(populatedCharts["publication-rate"]);
    visibility.mockReturnValue("visible");
    await act(async () => fireEvent(document, new Event("visibilitychange")));
    expect(screen.getByText("publication-rate ready")).toBeTruthy();
    expect(adminOverviewChart).toHaveBeenCalledTimes(2);
  } finally {
    visibility.mockRestore();
  }
});

it.each([new ApiError(500), new ApiError(503, undefined, {}, "analytics_unavailable")])(
  "stops preparation polling after %s and keeps its manual retry",
  async (failure) => {
    vi.useFakeTimers();
    vi.mocked(adminOverviewChart)
      .mockRejectedValueOnce(new ApiError(503, undefined, {}, "analytics_preparing"))
      .mockRejectedValueOnce(failure)
      .mockResolvedValue(populatedCharts["publication-rate"]);
    render(publication());
    await act(async () => {});
    await saveRefresh(0);
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(within(publicationCard()).getByRole("alert")).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(adminOverviewChart).toHaveBeenCalledTimes(2);
    await act(async () =>
      fireEvent.click(within(publicationCard()).getByRole("button", { name: "Try again" })),
    );
    expect(screen.getByText("publication-rate ready")).toBeTruthy();
  },
);

it("shows unavailable analytics as an error without automatic preparation polling", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart)
    .mockRejectedValueOnce(new ApiError(503, undefined, {}, "analytics_unavailable"))
    .mockResolvedValue(populatedCharts["publication-rate"]);
  render(publication());
  await act(async () => {});
  expect(within(publicationCard()).getByRole("alert")).toBeTruthy();
  expect(screen.queryByText("Still preparing this chart")).toBeNull();
  expect(screen.queryByRole("status", { name: "Loading Publication output" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(1);
  await act(async () =>
    fireEvent.click(within(publicationCard()).getByRole("button", { name: "Try again" })),
  );
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
});

it("cancels an in-flight completion check and stops polling when unmounted", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart)
    .mockRejectedValueOnce(new ApiError(503, undefined, {}, "analytics_preparing"))
    .mockImplementation(() => new Promise(() => {}));
  const view = render(publication());
  await act(async () => {});
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
  const signal = vi.mocked(adminOverviewChart).mock.calls[1][2]!.signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
});

it("cancels delayed preparation checks when the date range changes", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart).mockImplementation(async (_chart, params) => {
    if (params?.from === "now-29d/d") throw new ApiError(503, undefined, {}, "analytics_preparing");
    return reportForRange(populatedCharts["publication-rate"], params);
  });
  const view = render(publication());
  await act(async () => {});
  await saveRefresh(0);
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(3);
  view.rerender(publication({ from: "now-6d/d", to: "now" }));
  await act(async () => {});
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(4);
});

it("replaces a failed Queue pressure refresh with its own retry state", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart)
    .mockResolvedValueOnce(populatedCharts["queue-pressure"])
    .mockRejectedValue(new ApiError(500));
  render(
    <RefreshSettings>
      <OverviewChart chart="queue-pressure" range={{ from: "now-29d/d", to: "now" }} />
    </RefreshSettings>,
  );
  await act(async () => {});
  expect(screen.getByText("queue-pressure ready")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(screen.queryByText("queue-pressure ready")).toBeNull();
  const card = document.querySelector('[data-overview-card="queue-pressure"]') as HTMLElement;
  expect(within(card).getByRole("alert").textContent).toContain("Could not update queue pressure.");
});

it("keeps freshness metadata out of charts", async () => {
  vi.mocked(adminOverviewChart).mockResolvedValue({
    ...populatedCharts["publication-rate"],
    snapshot: { status: "stale", generated_at: "2026-10-26T13:26:00Z", lag_seconds: 600 },
  });
  render(publication());
  await screen.findByText("publication-rate ready");
  expect(screen.queryByText(/Last updated|^Updated |Checked .*ago/)).toBeNull();
});

it("offers an in-chart retry when a request exceeds its deadline", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewChart).mockImplementation(
    (_chart, _params, options) =>
      new Promise((_resolve, reject) => {
        options!.signal!.addEventListener("abort", () => reject(options!.signal!.reason), {
          once: true,
        });
      }),
  );
  render(publication());
  await act(async () => {});
  await act(() => vi.advanceTimersByTimeAsync(15_000));
  expect(within(publicationCard()).getByRole("alert").textContent).toContain(
    "Loading publication output took too long.",
  );
  expect(within(publicationCard()).getByRole("button", { name: "Try again" })).toBeTruthy();
});

it("does not carry another range's error or busy state back into a saved chart", async () => {
  vi.mocked(adminOverviewChart).mockImplementation(async (_chart, params) => {
    if (params?.from === "now-29d/d") throw new ApiError(503);
    return reportForRange(populatedCharts["publication-rate"], { from: "now-6d/d", to: "now" });
  });
  const view = render(publication({ from: "now-6d/d", to: "now" }));
  await screen.findByText("publication-rate ready");
  view.rerender(publication());
  await screen.findByRole("alert");
  view.rerender(publication({ from: "now-6d/d", to: "now" }));
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(document.querySelector("[data-overview-chart]")?.getAttribute("aria-busy")).toBe("false");
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
});

it("cancels a different range's pending read when returning to saved data", async () => {
  vi.mocked(adminOverviewChart).mockImplementation((_chart, params) =>
    params?.from === "now-29d/d"
      ? new Promise(() => {})
      : Promise.resolve(populatedCharts["publication-rate"]),
  );
  const view = render(publication({ from: "now-6d/d", to: "now" }));
  await screen.findByText("publication-rate ready");
  view.rerender(publication());
  await waitFor(() => expect(adminOverviewChart).toHaveBeenCalledTimes(2));
  const signal = vi.mocked(adminOverviewChart).mock.calls[1][2]!.signal!;
  view.rerender(publication({ from: "now-6d/d", to: "now" }));
  expect(signal.aborted).toBe(true);
  expect(screen.getByText("publication-rate ready")).toBeTruthy();
  expect(document.querySelector("[data-overview-chart]")?.getAttribute("aria-busy")).toBe("false");
});

it("rejects a response for a different chart instead of rendering it", async () => {
  vi.mocked(adminOverviewChart).mockResolvedValue(populatedCharts["publication-latency"]);
  render(publication());
  expect(await within(publicationCard()).findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("publication-latency ready")).toBeNull();
});

it.each([
  {
    label: "Today",
    range: { from: "now/d", to: "now" },
    previous: { from: "2026-10-29", to: "2026-10-29" },
    next: { from: "2026-10-30", to: "2026-10-30" },
  },
  {
    label: "Last seven days",
    range: { from: "now-6d/d", to: "now" },
    previous: { from: "2026-10-23", to: "2026-10-29" },
    next: { from: "2026-10-24", to: "2026-10-30" },
  },
])("rolls all twelve $label panels together at UTC midnight", async ({ range, previous, next }) => {
  vi.useFakeTimers();
  // The browser's local calendar can already be tomorrow; the contract remains UTC.
  vi.setSystemTime(new Date("2026-10-30T05:29:59+05:30"));
  vi.mocked(adminOverviewChart).mockImplementation(async (chart, params) =>
    reportForRange(populatedCharts[chart], params),
  );
  showOverview(range);
  await act(async () => {});
  expect(adminOverviewChart).toHaveBeenCalledTimes(12);
  expect(vi.mocked(adminOverviewChart).mock.calls.map(([, params]) => params)).toEqual(
    Array(12).fill(previous),
  );
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(24);
  expect(
    vi
      .mocked(adminOverviewChart)
      .mock.calls.slice(12)
      .map(([, params]) => params),
  ).toEqual(Array(12).fill(next));
  for (const chart of charts) {
    const result = screen.getByText(`${chart} ready`);
    expect(result.getAttribute("data-report-start")).toBe(next.from);
    expect(result.getAttribute("data-report-end")).toBe(next.to);
  }
});

it("aborts yesterday's pending requests at UTC rollover and ignores their late results", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-29T23:59:59Z"));
  const pending: {
    chart: keyof typeof populatedCharts;
    signal: AbortSignal;
    resolve: (report: OverviewChartReport) => void;
  }[] = [];
  vi.mocked(adminOverviewChart).mockImplementation((chart, params, options) =>
    chart === "publication-rate" && params?.to === "2026-10-29"
      ? new Promise((resolve) => pending.push({ chart, signal: options!.signal!, resolve }))
      : Promise.resolve(reportForRange(populatedCharts[chart], params)),
  );
  showOverview({ from: "now/d", to: "now" });
  await act(async () => {});
  expect(pending).toHaveLength(1);
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(pending.every(({ signal }) => signal.aborted)).toBe(true);
  await act(async () => {
    for (const { chart, resolve } of pending) {
      resolve({ ...populatedCharts[chart], start: "2026-10-29", end: "2026-10-29" });
    }
  });
  for (const chart of charts)
    expect(screen.getByText(`${chart} ready`).getAttribute("data-report-start")).toBe("2026-10-30");
});

it("resolves the current UTC day once on return after a hidden tab skips midnight", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-29T23:59:59Z"));
  vi.mocked(adminOverviewChart).mockImplementation(async (chart, params) =>
    reportForRange(populatedCharts[chart], params),
  );
  showOverview({ from: "now/d", to: "now" });
  await act(async () => {});
  expect(adminOverviewChart).toHaveBeenCalledTimes(12);
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  try {
    await act(async () => fireEvent(document, new Event("visibilitychange")));
    vi.setSystemTime(new Date("2026-10-31T00:00:00Z"));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(adminOverviewChart).toHaveBeenCalledTimes(12);
    visibility.mockReturnValue("visible");
    await act(async () => fireEvent(document, new Event("visibilitychange")));
    const resumed = vi.mocked(adminOverviewChart).mock.calls.slice(12);
    const current = resumed.filter(([, params]) => params?.to === "2026-10-31");
    const cancelled = resumed.filter(([, params]) => params?.to !== "2026-10-31");
    expect(current).toHaveLength(12);
    // Queue polling can resume before the parent clock listener; that old read
    // must be cancelled rather than mixed into the new day's results.
    for (const [chart, , options] of cancelled) {
      expect(chart).toBe("queue-pressure");
      expect(options!.signal!.aborted).toBe(true);
    }
    expect(current.map(([, params]) => params)).toEqual(
      Array(12).fill({ from: "2026-10-31", to: "2026-10-31" }),
    );
    for (const chart of charts)
      expect(screen.getByText(`${chart} ready`).getAttribute("data-report-start")).toBe(
        "2026-10-31",
      );
  } finally {
    visibility.mockRestore();
  }
});

it("keeps an absolute custom interval unchanged when the UTC date rolls over", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-29T23:59:59Z"));
  vi.mocked(adminOverviewChart).mockImplementation(async (chart, params) =>
    reportForRange(populatedCharts[chart], params),
  );
  const custom = { from: "2026-10-01", to: "2026-10-05" };
  showOverview(custom);
  await act(async () => {});
  expect(adminOverviewChart).toHaveBeenCalledTimes(12);
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(adminOverviewChart).toHaveBeenCalledTimes(12);
  expect(vi.mocked(adminOverviewChart).mock.calls.map(([, params]) => params)).toEqual(
    Array(12).fill(custom),
  );
});
