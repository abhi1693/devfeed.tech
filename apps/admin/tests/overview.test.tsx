// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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

it("shows current inventory, two charts, and links to the relevant review and AI queues", () => {
  render(<Overview initialData={populatedOverview} />);
  expect(screen.getByRole("link", { name: /Published articles 832/ }).getAttribute("href")).toBe("/content/articles?publication_status=published");
  expect(screen.getByRole("link", { name: /Active sources 42/ }).textContent).toContain("1 with recent fetch failures");
  expect(screen.getByRole("link", { name: "Topic proposals: 24 awaiting review" }).getAttribute("href")).toBe("/taxonomy/topics/proposals?status=pending");
  expect(screen.getByRole("link", { name: "Relationships: 6 awaiting review" }).getAttribute("href")).toBe("/taxonomy/relationships/proposals?status=pending");
  expect(screen.getByRole("link", { name: "18 queued" }).getAttribute("href")).toBe("/jobs/analysis?status=queued");
  expect(screen.getByRole("link", { name: "Review failed jobs" }).getAttribute("href")).toBe("/jobs/analysis?status=failed");
  expect(screen.getAllByRole("figure")).toHaveLength(2);
  expect(screen.getByText("60", { selector: "strong" })).toBeDefined();
  expect(screen.getByText("39", { selector: "strong" })).toBeDefined();
  expect(adminOverview).not.toHaveBeenCalled();
  expect(notify.success).not.toHaveBeenCalled();
});

it("shows honest empty states without a misleading success percentage or blank charts", () => {
  render(<Overview initialData={emptyOverview} />);
  expect(screen.getByText("No content activity yet")).toBeDefined();
  expect(screen.getByText("Your topic coverage starts here")).toBeDefined();
  expect(screen.getByText(/You’re all caught up/)).toBeDefined();
  expect(screen.getByText("Completed runs will appear here as analysis finishes.")).toBeDefined();
  expect(screen.queryByRole("figure")).toBeNull();
  expect(screen.queryByText(/100%/)).toBeNull();
});

it("updates the date range only when the new snapshot arrives, retaining inventory totals", async () => {
  let resolve!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  render(<Overview initialData={populatedOverview} />);
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  expect(adminOverview).toHaveBeenCalledWith({ days: 7 }, { signal: expect.any(AbortSignal) });
  expect(screen.getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("true");
  expect((screen.getByRole("button", { name: "7 days" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toBe("Updating overview…");
  await act(async () => resolve({ ...populatedOverview, days: 7, analysis: { ...populatedOverview.analysis, succeeded: 92 } }));
  expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("Finished in 7 days")).toBeDefined();
  expect(screen.getByRole("link", { name: /Published articles 832/ })).toBeDefined();
  expect(notify.success).not.toHaveBeenCalled();
});

it("preserves the previous snapshot and range after an error, with a working retry", async () => {
  vi.mocked(adminOverview).mockRejectedValueOnce(new Error("Unavailable"));
  render(<Overview initialData={populatedOverview} />);
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Showing the last successful snapshot");
  expect(screen.getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("link", { name: /Published articles 832/ })).toBeDefined();
  vi.mocked(adminOverview).mockResolvedValueOnce({ ...populatedOverview, days: 7 });
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(notifyFailure).toHaveBeenCalledOnce();
  expect(vi.mocked(adminOverview).mock.lastCall?.[0]).toEqual({ days: 7 });
});

it("cancels requests and ignores their result when leaving the page", async () => {
  let resolve!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = render(<Overview initialData={populatedOverview} />);
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
  render(<Overview initialData={populatedOverview} />);
  expect(screen.getByRole("combobox", { name: "Refresh interval" }).textContent).toBe("10s");
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

it("offers intervals up to one minute and Off cancels the timer", async () => {
  vi.useFakeTimers();
  vi.mocked(adminOverview).mockResolvedValue(populatedOverview);
  render(<Overview initialData={populatedOverview} />);
  const select = screen.getByRole("combobox", { name: "Refresh interval" });
  fireEvent.click(select);
  expect(screen.getAllByRole("option").map(option => option.textContent)).toEqual(["Off", "5s", "10s", "15s", "30s", "1 min"]);
  fireEvent.click(screen.getByRole("option", { name: "1 min" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(59999); });
  expect(adminOverview).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  fireEvent.click(select);
  fireEvent.click(screen.getByRole("option", { name: "Off" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  // Pausing the timer does not prevent a deliberate date-range change.
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  await act(async () => {});
  expect(adminOverview).toHaveBeenCalledTimes(2);
  fireEvent.click(select);
  fireEvent.click(screen.getByRole("option", { name: "5s" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(adminOverview).toHaveBeenCalledTimes(3);
});

it("avoids overlapping slow requests and lets Off cancel an in-flight automatic refresh", async () => {
  vi.useFakeTimers();
  let resolve!: (value: typeof populatedOverview) => void;
  vi.mocked(adminOverview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  render(<Overview initialData={populatedOverview} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(40000); });
  expect(adminOverview).toHaveBeenCalledTimes(1);
  const signal = vi.mocked(adminOverview).mock.calls[0][1]?.signal;
  fireEvent.click(screen.getByRole("combobox", { name: "Refresh interval" }));
  fireEvent.click(screen.getByRole("option", { name: "Off" }));
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve({ ...populatedOverview, articles_published: 999 }));
  expect(screen.getByRole("link", { name: /Published articles 832/ })).toBeDefined();
  expect(screen.getByRole("region", { name: "Application overview" }).getAttribute("aria-busy")).toBe("false");
});

it("skips hidden tabs and cleans up the timer on unmount", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  vi.mocked(adminOverview).mockResolvedValue(populatedOverview);
  const view = render(<Overview initialData={populatedOverview} />);
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
  render(<Overview initialData={populatedOverview} />);
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
