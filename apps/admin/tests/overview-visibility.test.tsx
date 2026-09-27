// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OverviewPanel } from "@/components/organisms/overview-panel";
import { adminOverviewPanel } from "@/lib/api/generated/admin";
import { populatedOverview } from "./fixtures/overview";

vi.mock("@/lib/api/generated/admin", () => ({ adminOverviewPanel: vi.fn() }));
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
function panel(days = 30) {
  return (
    <OverviewPanel panel="publications" title="Publications" days={days} refresh={0}>
      {(data) => <div>Loaded {data.days}</div>}
    </OverviewPanel>
  );
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
  vi.mocked(adminOverviewPanel).mockImplementation(async (_panel, params) => ({
    ...populatedOverview,
    days: params!.days!,
  }));
  const view = render(panel());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(adminOverviewPanel).not.toHaveBeenCalled();
  await visibility(true, false);
  expect(adminOverviewPanel).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Loaded 30")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(adminOverviewPanel).toHaveBeenCalledTimes(1);
  await visibility(true, true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(adminOverviewPanel).toHaveBeenCalledTimes(2);
  await visibility(false, false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(adminOverviewPanel).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Loaded 30")).toBeTruthy();
  view.rerender(panel(7));
  expect(adminOverviewPanel).toHaveBeenCalledTimes(2);
  await visibility(true, true);
  expect(screen.getByText("Loaded 7")).toBeTruthy();
  expect(adminOverviewPanel).toHaveBeenCalledTimes(3);
});

it("aborts offscreen requests and ignores their late results", async () => {
  observe();
  let finish!: (data: typeof populatedOverview) => void;
  vi.mocked(adminOverviewPanel).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(panel());
  await visibility(true, true);
  const signal = vi.mocked(adminOverviewPanel).mock.calls[0][2]!.signal!;
  await visibility(false, false);
  expect(signal.aborted).toBe(true);
  await act(async () => finish(populatedOverview));
  expect(screen.queryByText("Loaded 30")).toBeNull();
});
