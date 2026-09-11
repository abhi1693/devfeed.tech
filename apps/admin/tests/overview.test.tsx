// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RefreshSettings, saveRefresh } from "./refresh-settings";
import { Overview } from "@/components/organisms/overview";
import { adminOverview } from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import { notify, notifyFailure } from "@/lib/notifications";
import { emptyOverview, populatedOverview } from "./fixtures/overview";

vi.mock("@/lib/api/generated/admin", () => ({ adminOverview: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no layout; the browser checks exercise real responsive sizing.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 600, height: 256, top: 0, left: 0, right: 600, bottom: 256, x: 0, y: 0, toJSON: () => ({}) });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("shows reader and publication charts with actionable user and source details", () => {
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  expect(screen.getByText("First publications").closest("a")).toBeNull();
  expect(screen.getByRole("link", { name: /relationship proposals/ }).getAttribute("href")).toBe("/taxonomy/relationships/proposals?status=pending");
  expect(screen.getByText("Not needed")).toBeTruthy();
  expect(screen.getByRole("figure", { name: "Personalized feed health" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Ada" }).getAttribute("href")).toBe("/users/user-1/analysis");
  expect(screen.getByRole("link", { name: "All sources" }).getAttribute("href")).toBe("/content/sources");
  expect(screen.getByRole("heading", { name: "Source output" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByRole("figure", { name: "Daily article preview opens" }).closest("details")).toBeNull();
  expect(screen.getByRole("figure", { name: "Daily articles discovered and first published" }).closest("details")).toBeNull();
  expect(screen.getByRole("link", { name: /Python.*No new content/ })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Interest versus coverage" })).toBeTruthy();
  expect(screen.getByRole("figure", { name: "Job outcome rates" })).toBeTruthy();
  expect(screen.getByRole("figure", { name: "Current workload" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByRole("heading", { name: "Reading concentration" })).toBeTruthy();
  expect(adminOverview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Content types" }));
  expect(screen.queryByText("First publications by content type.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "About Publishing activity" }));
  expect(screen.getByRole("tooltip").textContent).toContain("First publications by content type.");
  expect(screen.getByRole("button", { name: "Content types" }).getAttribute("aria-pressed")).toBe("true");
});

it("shows honest empty states without a misleading success percentage or blank charts", () => {
  render(<Overview initialData={emptyOverview} />);
  expect(screen.getByText("No publishing activity in this period.")).toBeDefined();
  expect(screen.getByText("No recorded preview opens in the available history.")).toBeDefined();
  expect(screen.queryByText("Needs attention")).toBeNull();
  expect(within(screen.getByRole("region", { name: "Application overview" })).queryByText(/100%/)).toBeNull();
});

it("updates the date range only when the new snapshot arrives, retaining inventory totals", async () => {
  let resolve!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  expect(adminOverview).toHaveBeenCalledWith({ days: 7 }, { signal: expect.any(AbortSignal) });
  expect(screen.getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("true");
  expect((screen.getByRole("button", { name: "7 days" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toBe("Updating overview…");
  await act(async () => resolve({ ...populatedOverview, days: 7, analysis: { ...populatedOverview.analysis, succeeded: 92 } }));
  expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("First publications")).toBeDefined();
  expect(notify.success).not.toHaveBeenCalled();
});

it("preserves the previous snapshot and range after an error, with a working retry", async () => {
  vi.mocked(adminOverview).mockRejectedValueOnce(new Error("Unavailable"));
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Showing the last successful snapshot");
  expect(screen.getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("First publications")).toBeDefined();
  vi.mocked(adminOverview).mockResolvedValueOnce({ ...populatedOverview, days: 7 });
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(notifyFailure).toHaveBeenCalledOnce();
  expect(vi.mocked(adminOverview).mock.lastCall?.[0]).toEqual({ days: 7 });
});

it("cancels requests and ignores their result when leaving the page", async () => {
  let resolve!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  const signal = vi.mocked(adminOverview).mock.calls[0][1]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(populatedOverview));
  expect(notify.success).not.toHaveBeenCalled();
});

it("defaults to quiet 10-second refreshes and follows the selected date range", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverview).mockImplementation(async params => ({ ...populatedOverview, days: params?.days ?? 30 }));
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  expect(screen.queryByRole("combobox", { name: "Refresh interval" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
  expect(adminOverview).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(adminOverview).toHaveBeenCalledWith({ days: 30 }, { signal: expect.any(AbortSignal) });
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(vi.mocked(adminOverview).mock.lastCall?.[0]).toEqual({ days: 7 });
  expect(notify.success).not.toHaveBeenCalled();
});

it("follows the saved interval and Off cancels the timer", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverview).mockResolvedValue(populatedOverview);
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  await saveRefresh(60);
  await act(async () => { await vi.advanceTimersByTimeAsync(59999); });
  expect(adminOverview).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  await saveRefresh(0);
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  // Pausing the timer does not prevent a deliberate date-range change.
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  await act(async () => {});
  expect(adminOverview).toHaveBeenCalledTimes(2);
  await saveRefresh(5);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(adminOverview).toHaveBeenCalledTimes(3);
});

it("avoids overlapping slow requests and lets Off cancel an in-flight automatic refresh", async () => {
  vi.useFakeTimers();
  let resolve!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  await act(async () => { await vi.advanceTimersByTimeAsync(40000); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  const signal = vi.mocked(adminOverview).mock.calls[0][1]?.signal;
  await saveRefresh(0);
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve({ ...populatedOverview, articles_published: 999 }));
  expect(screen.getByText("First publications")).toBeDefined();
  expect(screen.getByRole("region", { name: "Application overview" }).getAttribute("aria-busy")).toBe("false");
});

it("skips hidden tabs and cleans up the timer on unmount", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  vi.mocked(adminOverview).mockResolvedValue(populatedOverview);
  const view = render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(adminOverview).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
});

it("keeps repeated polling failures quiet while preserving session-expiry handling and recovery", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverview).mockRejectedValue(new ApiError(503));
  render(<RefreshSettings><Overview initialData={populatedOverview} /></RefreshSettings>);
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(notifyFailure).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("alert").textContent).toContain("last successful snapshot");
  vi.mocked(adminOverview).mockRejectedValueOnce(new ApiError(401));
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(notifyFailure).toHaveBeenLastCalledWith(expect.objectContaining({ status: 401 }), "Could not refresh overview");
  vi.mocked(adminOverview).mockResolvedValueOnce(populatedOverview);
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(notify.success).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(notifyFailure).toHaveBeenCalledTimes(3);
});
