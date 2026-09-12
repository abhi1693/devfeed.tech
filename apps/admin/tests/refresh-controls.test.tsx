// @vitest-environment jsdom
import { useCallback, useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { SettingsProvider } from "@/lib/use-settings";
import { useRequest } from "@/lib/use-request";
import { RefreshSettings, saveRefresh } from "./refresh-settings";
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
const load = vi.fn<(signal: AbortSignal) => Promise<string>>();
function Reader({ id = "articles" }: { id?: string }) {
  const seconds = useRefreshInterval();
  const request = useCallback((signal: AbortSignal) => load(signal), []);
  const result = useRequest(id, request, seconds * 1000);
  return <><p>{result.data}</p><input aria-label={`${id} draft`} /></>;
}
const advance = (milliseconds: number) => act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); load.mockResolvedValue("Loaded data"); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("uses 10 seconds by default without page controls and preserves drafts through settings changes", async () => {
  await act(async () => { render(<RefreshSettings><Reader /></RefreshSettings>); });
  expect(screen.queryByRole("combobox", { name: /refresh/i })).toBeNull();
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole("textbox", { name: "articles draft" }), { target: { value: "Unsubmitted filter" } });
  await advance(10000); expect(load).toHaveBeenCalledTimes(2);
  await saveRefresh(0); await advance(120000); expect(load).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Loaded data")).toBeDefined();
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Unsubmitted filter");
  await saveRefresh(60);
  await advance(59999); expect(load).toHaveBeenCalledTimes(2);
  await advance(1); expect(load).toHaveBeenCalledTimes(3);
});

it("uses the persisted account interval on initial load", async () => {
  await act(async () => { render(<SettingsProvider admin={{ subject: "test", issuer: "fixture", organization_id: "test", roles: [], expires_at: 4102444800, csrf_token: "fixture" }} initial={{ defaults: { refresh_seconds: 60 } }}><Reader /></SettingsProvider>); });
  expect(load).toHaveBeenCalledOnce();
  await advance(59999); expect(load).toHaveBeenCalledOnce();
  await advance(1); expect(load).toHaveBeenCalledTimes(2);
});

it("uses the same saved interval for every panel and after page navigation", async () => {
  function Pages() { const [page, setPage] = useState("articles"); return <RefreshSettings><button onClick={() => setPage("topics")}>Next page</button><Reader key={page} id={page} /><Reader id="logs" /></RefreshSettings>; }
  await act(async () => { render(<Pages />); });
  await saveRefresh(0);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next page" })); });
  const count = load.mock.calls.length;
  await advance(60000); expect(load).toHaveBeenCalledTimes(count);
  await saveRefresh(5); await advance(5000); expect(load).toHaveBeenCalledTimes(count + 2);
});

it("prevents overlapping reads and aborts automatic requests when the saved preference becomes Off", async () => {
  await act(async () => { render(<RefreshSettings><Reader /></RefreshSettings>); });
  let resolve!: (value: string) => void;
  load.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await advance(40000); expect(load).toHaveBeenCalledTimes(2);
  const signal = load.mock.lastCall![0];
  await saveRefresh(0); expect(signal.aborted).toBe(true);
  await act(async () => resolve("Stale result"));
  expect(screen.queryByText("Stale result")).toBeNull();
  expect(screen.getByText("Loaded data")).toBeDefined();
});

it("skips hidden tabs and cancels timers on unmount", async () => {
  const hidden = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<Reader />); });
  await advance(30000); expect(load).not.toHaveBeenCalled();
  hidden.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(load).toHaveBeenCalledTimes(1);
  await advance(10000); expect(load).toHaveBeenCalledTimes(2);
  view.unmount(); expect(load.mock.lastCall![0].aborted).toBe(true);
  await advance(30000); expect(load).toHaveBeenCalledTimes(2);
});
