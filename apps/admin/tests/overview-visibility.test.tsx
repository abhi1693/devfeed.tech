// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OverviewChart } from "@/components/organisms/overview-chart";
import { adminOverviewChart } from "@/lib/api/generated/admin";
import { populatedCharts, reportForRange } from "./fixtures/overview";
import type { DateRange } from "@devfeed/ui/date-range-picker";
const publishingReport = populatedCharts["publication-rate"];

vi.mock("@/lib/api/generated/admin", () => ({ adminOverviewChart: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
vi.mock("@/lib/use-refresh-interval", () => ({ useRefreshInterval: () => 5 }));
const observers: { callback: IntersectionObserverCallback; margin?: string }[] = [];
function observe() {
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
        observers.push({ callback, margin: options?.rootMargin });
      }
      observe() {}
      disconnect() {}
    },
  );
}
async function visibility(near: boolean, visible: boolean) {
  await act(async () => {
    for (const observer of observers)
      observer.callback(
        [{ isIntersecting: observer.margin ? near : visible } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
  });
}
function panel(range: DateRange = { from: "now-29d/d", to: "now" }) {
  return <OverviewChart chart="publication-rate" range={range} />;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.clearAllMocks();
  observers.length = 0;
});

it("loads near the viewport, polls only onscreen, and defers offscreen range changes", async () => {
  vi.useFakeTimers();
  observe();
  vi.mocked(adminOverviewChart).mockImplementation(async (_panel, params) =>
    reportForRange(publishingReport, params),
  );
  const view = render(panel());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(adminOverviewChart).not.toHaveBeenCalled();
  await visibility(true, false);
  expect(adminOverviewChart).toHaveBeenCalledTimes(1);
  expect(screen.getByText("42")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(adminOverviewChart).toHaveBeenCalledTimes(1);
  await visibility(true, true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
  await visibility(false, false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
  expect(screen.getByText("42")).toBeTruthy();
  await visibility(true, false);
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
  await visibility(false, false);
  view.rerender(panel({ from: "now-6d/d", to: "now" }));
  expect(adminOverviewChart).toHaveBeenCalledTimes(2);
  await visibility(true, true);
  expect(screen.getByText("42")).toBeTruthy();
  expect(adminOverviewChart).toHaveBeenCalledTimes(3);
});

it("finishes admitted cold reads offscreen and reuses them when layout changes bring them back", async () => {
  observe();
  let finish!: (data: typeof publishingReport) => void;
  vi.mocked(adminOverviewChart).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(panel());
  await visibility(true, true);
  const signal = vi.mocked(adminOverviewChart).mock.calls[0][2]!.signal!;
  await visibility(false, false);
  expect(signal.aborted).toBe(false);
  await act(async () => finish(publishingReport));
  expect(screen.getByText("42")).toBeTruthy();
  await visibility(true, true);
  expect(adminOverviewChart).toHaveBeenCalledTimes(1);
});

it("aborts admitted reads on unmount while ignoring a late response", async () => {
  observe();
  let finish!: (data: typeof publishingReport) => void;
  vi.mocked(adminOverviewChart).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(panel());
  await visibility(true, false);
  const signal = vi.mocked(adminOverviewChart).mock.calls[0][2]!.signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish(publishingReport));
  expect(screen.queryByText("42")).toBeNull();
});
