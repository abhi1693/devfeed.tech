// @vitest-environment jsdom
import { useCallback, useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RefreshInterval } from "@/components/molecules/refresh-interval";
import { RefreshIntervalProvider, useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
const load = vi.fn<(signal: AbortSignal) => Promise<string>>();
function Reader({ id = "articles", label = "Refresh interval" }: { id?: string; label?: string }) {
  const [seconds, setSeconds] = useRefreshInterval();
  const request = useCallback((signal: AbortSignal) => load(signal), []);
  const result = useRequest(id, request, seconds * 1000);
  return <><RefreshInterval value={seconds} onChange={setSeconds} label={label} loading={result.loading || result.refreshing} /><p>{result.data}</p><input aria-label={`${id} draft`} /></>;
}
function change(label: string, value: string) { fireEvent.click(screen.getByRole("combobox", { name: label })); fireEvent.click(screen.getByRole("option", { name: value })); }
const advance = (milliseconds: number) => act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); load.mockResolvedValue("Loaded data"); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("makes fast refreshes visible while releasing the busy state as soon as data arrives", async () => {
  await act(async () => { render(<Reader />); });
  await advance(700);
  const control = screen.getByRole("combobox", { name: "Refresh interval" });
  const icon = () => control.querySelector("svg")!;
  expect(icon().classList.contains("animate-spin")).toBe(false);
  let resolve!: (value: string) => void;
  load.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await advance(9300);
  expect(control.getAttribute("aria-busy")).toBe("true");
  expect(icon().classList.contains("animate-spin")).toBe(true);
  expect((control as HTMLButtonElement).disabled).toBe(false);
  await advance(20);
  await act(async () => resolve("Refreshed data"));
  expect(screen.getByText("Refreshed data")).toBeDefined();
  expect(control.getAttribute("aria-busy")).toBe("false");
  await advance(699);
  expect(icon().classList.contains("animate-spin")).toBe(true);
  await advance(1);
  expect(icon().classList.contains("animate-spin")).toBe(false);
});

it("keeps spinning for slow refreshes and stops immediately when automatic refresh is turned off", async () => {
  await act(async () => { render(<Reader />); });
  load.mockImplementationOnce(() => new Promise(() => {}));
  await advance(12000);
  const control = screen.getByRole("combobox", { name: "Refresh interval" });
  expect(control.querySelector("svg")!.classList.contains("animate-spin")).toBe(true);
  change("Refresh interval", "Off");
  expect(control.querySelector("svg")!.classList.contains("animate-spin")).toBe(false);
  expect(control.getAttribute("aria-busy")).toBe("false");
});

it("uses 10 seconds by default, preserves drafts and data, and Off starts no extra request", async () => {
  await act(async () => { render(<Reader />); });
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole("textbox", { name: "articles draft" }), { target: { value: "Unsubmitted filter" } });
  await advance(10000);
  expect(load).toHaveBeenCalledTimes(2);
  change("Refresh interval", "Off");
  await advance(120000);
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Loaded data")).toBeDefined();
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Unsubmitted filter");
  change("Refresh interval", "1 min");
  await advance(59999); expect(load).toHaveBeenCalledTimes(2);
  await advance(1); expect(load).toHaveBeenCalledTimes(3);
});

it("shares the selected interval between panels and retains it on page navigation", async () => {
  function Pages() { const [page, setPage] = useState("articles"); return <RefreshIntervalProvider><button onClick={() => setPage("topics")}>Next page</button><Reader key={page} id={page} /><Reader id="logs" label="Log refresh interval" /></RefreshIntervalProvider>; }
  await act(async () => { render(<Pages />); });
  change("Refresh interval", "Off");
  expect(screen.getByRole("combobox", { name: "Log refresh interval" }).textContent).toBe("Off");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next page" })); });
  expect(screen.getByRole("combobox", { name: "Refresh interval" }).textContent).toBe("Off");
  const count = load.mock.calls.length;
  await advance(60000); expect(load).toHaveBeenCalledTimes(count);
});

it("prevents overlapping reads, aborts automatic requests on Off and ignores their late results", async () => {
  await act(async () => { render(<Reader />); });
  let resolve!: (value: string) => void;
  load.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await advance(40000);
  expect(load).toHaveBeenCalledTimes(2);
  const signal = load.mock.lastCall![0];
  change("Refresh interval", "Off");
  expect(signal.aborted).toBe(true);
  await act(async () => resolve("Stale result"));
  expect(screen.queryByText("Stale result")).toBeNull();
  expect(screen.getByText("Loaded data")).toBeDefined();
});

it("skips hidden tabs, aborts on navigation and cancels timers on unmount", async () => {
  const hidden = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<Reader />); });
  await advance(30000); expect(load).toHaveBeenCalledTimes(1);
  hidden.mockReturnValue("visible");
  await advance(10000); expect(load).toHaveBeenCalledTimes(2);
  view.unmount();
  expect(load.mock.lastCall![0].aborted).toBe(true);
  await advance(30000); expect(load).toHaveBeenCalledTimes(2);
});
