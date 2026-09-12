// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePolling } from "@/lib/use-polling";
import { useRequest } from "@/lib/use-request";

vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
afterEach(() => { cleanup(); vi.useRealTimers(); });
const event = async (target: Window | Document, name: string) => {
  await act(async () => { target.dispatchEvent(new Event(name)); });
};

it("pauses on blur and coalesces focus plus visibility into one immediate refresh", async () => {
  vi.useFakeTimers();
  const load = vi.fn<(signal: AbortSignal) => Promise<void>>(async () => {});
  renderHook(() => usePolling(load, 1000));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(load).toHaveBeenCalledTimes(1);
  await event(window, "blur");
  expect(load.mock.calls[0][0].aborted).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(load).toHaveBeenCalledTimes(1);
  await event(window, "focus");
  await event(document, "visibilitychange");
  await event(window, "focus");
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(load).toHaveBeenCalledTimes(3);
});

it("waits for both visibility and focus before fetching an initially inactive page", async () => {
  vi.useFakeTimers();
  const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  const focus = vi.spyOn(document, "hasFocus").mockReturnValue(false);
  const load = vi.fn(async () => "loaded");
  const view = renderHook(() => useRequest("example", load, 1000));
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(load).not.toHaveBeenCalled();
  visible.mockReturnValue("visible");
  await event(document, "visibilitychange");
  expect(load).not.toHaveBeenCalled();
  focus.mockReturnValue(true);
  await event(window, "focus");
  expect(load).toHaveBeenCalledTimes(1);
  expect(view.result.current.data).toBe("loaded");
});

it("aborts a slow poll and prevents its stale completion from scheduling another timer", async () => {
  vi.useFakeTimers();
  let finish!: () => void;
  const load = vi.fn<(signal: AbortSignal) => Promise<void>>(async () => {}).mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  const view = renderHook(() => usePolling(load, 1000));
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(load).toHaveBeenCalledTimes(1);
  await event(window, "blur");
  expect(load.mock.calls[0][0].aborted).toBe(true);
  await event(window, "focus");
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { finish(); await vi.advanceTimersByTimeAsync(1000); });
  expect(load).toHaveBeenCalledTimes(3);
  view.unmount();
  await event(window, "blur");
  await event(window, "focus");
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(load).toHaveBeenCalledTimes(3);
});

it("does not enable polling when the saved interval is Off", async () => {
  vi.useFakeTimers();
  const load = vi.fn(async () => "loaded");
  await act(async () => { renderHook(() => useRequest("example", load, 0)); });
  await event(window, "blur");
  await event(window, "focus");
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(load).toHaveBeenCalledTimes(1);
});
