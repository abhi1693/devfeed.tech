// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RefreshSettings } from "./refresh-settings";
import { Overview } from "@/components/organisms/overview";
import { OverviewPanel } from "@/components/organisms/overview-panel";
import { adminOverviewPanel } from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import { populatedOverview } from "./fixtures/overview";

vi.mock("@/lib/api/generated/admin", () => ({ adminOverviewPanel: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
// Chart behavior is covered by the chart component suites; these tests exercise request isolation.
vi.mock("@/components/organisms/overview-metrics", () => ({
  OverviewMetrics: ({ metric }: { metric: number }) => <div>Metric {metric} ready</div>,
}));
vi.mock("@/components/organisms/overview-charts", () => ({
  OverviewCharts: () => <div>Activity ready</div>,
}));
vi.mock("@/components/organisms/overview-engagement-charts", () => ({
  OverviewEngagementCharts: () => <div>Engagement ready</div>,
}));
vi.mock("@/components/organisms/overview-panels", () =>
  Object.fromEntries(
    [
      "OverviewAttention",
      "OverviewAudience",
      "OverviewSources",
      "OverviewJobReliability",
      "OverviewWorkload",
      "OverviewPublicationAutomation",
      "OverviewBlockers",
    ].map((name) => [name, () => <div>{name} ready</div>]),
  ),
);
vi.mock("@/components/organisms/overview-token-chart", () => ({
  OverviewTokenChart: () => <div>Tokens ready</div>,
}));
vi.mock("@/components/organisms/overview-inference-charts", () => ({
  OverviewInferenceCharts: () => <div>Inference ready</div>,
}));

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
function showOverview() {
  return render(
    <RefreshSettings>
      <Overview initialDays={30} />
    </RefreshSettings>,
  );
}

it("shows the shell and one shimmer for every chart before data arrives", async () => {
  vi.mocked(adminOverviewPanel).mockImplementation(
    (_panel, _params, options) =>
      new Promise((_resolve, reject) =>
        options?.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        ),
      ),
  );
  showOverview();
  expect(screen.getByRole("heading", { name: "Overview" })).toBeTruthy();
  expect(screen.getAllByRole("status", { name: /^Loading / })).toHaveLength(33);
  await waitFor(() => expect(adminOverviewPanel).toHaveBeenCalledTimes(4));
});

it("renders completed charts while another chart is still loading", async () => {
  let finish!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverviewPanel).mockImplementation((panel) =>
    panel === "publications"
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : Promise.resolve(populatedOverview),
  );
  showOverview();
  await screen.findByText("Metric 1 ready");
  expect(screen.getByRole("status", { name: "Loading First publications" })).toBeTruthy();
  expect(screen.queryByText("Metric 0 ready")).toBeNull();
  await act(async () => finish(populatedOverview));
  expect(await screen.findByText("Metric 0 ready")).toBeTruthy();
  await waitFor(() => expect(adminOverviewPanel).toHaveBeenCalledTimes(33));
  expect(new Set(vi.mocked(adminOverviewPanel).mock.calls.map(([name]) => name)).size).toBe(33);
});

it("retries only the failed chart without reloading successful charts", async () => {
  vi.mocked(adminOverviewPanel).mockImplementation((panel) =>
    panel === "publications"
      ? Promise.reject(new ApiError(500))
      : Promise.resolve(populatedOverview),
  );
  showOverview();
  const retry = await screen.findByRole("button", { name: "Try again" });
  await waitFor(() => expect(adminOverviewPanel).toHaveBeenCalledTimes(33));
  vi.mocked(adminOverviewPanel).mockResolvedValue(populatedOverview);
  fireEvent.click(retry);
  expect(await screen.findByText("Metric 0 ready")).toBeTruthy();
  expect(adminOverviewPanel).toHaveBeenCalledTimes(34);
  expect(vi.mocked(adminOverviewPanel).mock.calls[33][0]).toBe("publications");
});

it("cancels old date-range requests and ignores their late results", async () => {
  const old: { resolve: (v: typeof populatedOverview) => void; signal: AbortSignal }[] = [];
  vi.mocked(adminOverviewPanel).mockImplementation((_panel, params, options) =>
    params?.days === 30
      ? new Promise((resolve) => old.push({ resolve, signal: options!.signal as AbortSignal }))
      : Promise.resolve({ ...populatedOverview, days: 7 }),
  );
  showOverview();
  await waitFor(() => expect(old).toHaveLength(4));
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  expect(old.every((x) => x.signal.aborted)).toBe(true);
  await act(async () => old.forEach((x) => x.resolve(populatedOverview)));
  await screen.findByText("Metric 0 ready");
  expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");
  await waitFor(() =>
    expect(
      vi.mocked(adminOverviewPanel).mock.calls.filter(([, params]) => params?.days === 7),
    ).toHaveLength(33),
  );
});

it("automatically retries an in-progress chart response", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverviewPanel)
    .mockRejectedValueOnce(new ApiError(503))
    .mockResolvedValue(populatedOverview);
  render(
    <RefreshSettings>
      <OverviewPanel panel="publications" title="Publications" days={30} refresh={0}>
        {() => <div>Loaded</div>}
      </OverviewPanel>
    </RefreshSettings>,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText("Loaded")).toBeTruthy();
  expect(adminOverviewPanel).toHaveBeenCalledTimes(2);
});

it("retains the last successful result when that chart refresh fails", async () => {
  vi.mocked(adminOverviewPanel).mockResolvedValue(populatedOverview);
  const view = render(
    <RefreshSettings>
      <OverviewPanel panel="publications" title="Publications" days={30} refresh={0}>
        {() => <div>Saved chart</div>}
      </OverviewPanel>
    </RefreshSettings>,
  );
  await screen.findByText("Saved chart");
  vi.mocked(adminOverviewPanel).mockRejectedValue(new ApiError(500));
  view.rerender(
    <RefreshSettings>
      <OverviewPanel panel="publications" title="Publications" days={30} refresh={1}>
        {() => <div>Saved chart</div>}
      </OverviewPanel>
    </RefreshSettings>,
  );
  await screen.findByRole("alert");
  expect(screen.getByText("Saved chart")).toBeTruthy();
});

it("groups charts into named sections and reports one honest refresh state", async () => {
  vi.mocked(adminOverviewPanel).mockResolvedValue({
    ...populatedOverview,
    generated_at: new Date().toISOString(),
  });
  showOverview();
  for (const name of [
    "Needs attention",
    "Publishing",
    "Audience",
    "Personalization",
    "Processing",
    "AI usage",
  ])
    expect(screen.getByRole("heading", { level: 2, name })).toBeTruthy();
  expect(screen.getByRole("navigation", { name: "Overview sections" })).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByRole("status", { name: "Overview refresh status" }).textContent).toContain(
      "Last refreshed",
    ),
  );
  expect(screen.getAllByText(/Last refreshed/)).toHaveLength(1);
  expect(screen.queryByText(/^Updated /)).toBeNull();
  vi.mocked(adminOverviewPanel).mockImplementation((panel) =>
    panel === "publications"
      ? Promise.reject(new ApiError(500))
      : Promise.resolve({ ...populatedOverview, generated_at: new Date().toISOString() }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh all" }));
  await waitFor(() =>
    expect(screen.getByRole("status", { name: "Overview refresh status" }).textContent).toContain(
      "1 panel needs attention",
    ),
  );
  expect(screen.queryByText(/Last refreshed/)).toBeNull();
  expect(screen.getByText(/Stale data/)).toBeTruthy();
});

it("does not call a partial refresh complete while a panel is pending", async () => {
  let finish!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverviewPanel).mockImplementation((panel) =>
    panel === "publications"
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : Promise.resolve({ ...populatedOverview, generated_at: new Date().toISOString() }),
  );
  showOverview();
  await waitFor(() => expect(adminOverviewPanel).toHaveBeenCalledTimes(33));
  expect(screen.getByRole("status", { name: "Overview refresh status" }).textContent).toContain(
    "Refreshing",
  );
  expect(screen.queryByText(/Last refreshed/)).toBeNull();
  await act(async () => finish({ ...populatedOverview, generated_at: new Date().toISOString() }));
  await waitFor(() =>
    expect(screen.getByRole("status", { name: "Overview refresh status" }).textContent).toContain(
      "Last refreshed",
    ),
  );
});
