// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RefreshSettings, saveRefresh } from "./refresh-settings";
import { JobLogs } from "@/components/organisms/job-logs";
import { adminJobLogs } from "@/lib/api/generated/admin";
import type { AdminJobLogs, JobLogEntry } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { toast } from "sonner";
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));

vi.mock("@/lib/api/generated/admin", () => ({ adminJobLogs: vi.fn() }));
vi.mock("@/lib/api/client", async original => ({ ...await original<typeof import("@/lib/api/client")>(), returnToLogin: vi.fn() }));
const entry = (id: string, message = "Fetching feed"): JobLogEntry => ({ id, message, timestamp: "2026-09-07T10:00:00Z", level: "INFO", fields: { attempt: 1, event: "ingestion_started" } });
const page = (overrides: Partial<AdminJobLogs> = {}): AdminJobLogs => ({ items: [], next_cursor: null, has_more: false, truncated: false, unreadable_entries: 0, retention_seconds: 604800, max_entries: 1000, job_status: "running", attempts: 1, ...overrides });
const advance = (ms = 10000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const mount = () => act(async () => { render(<JobLogs kind="ingestion" id="job-1" />); });

beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); vi.mocked(adminJobLogs).mockResolvedValue(page()); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("runtime log viewer", () => {
  it("polls using the cursor and appends entries without duplicates", async () => {
    vi.mocked(adminJobLogs).mockResolvedValueOnce(page({ items: [entry("1-0")], next_cursor: "1-0" }));
    await mount();
    expect(screen.getByText("Fetching feed")).toBeDefined();
    vi.mocked(adminJobLogs).mockResolvedValue(page({ items: [entry("1-0"), entry("2-0", "Ingested 10 entries")], next_cursor: "2-0" }));
    await advance();
    expect(adminJobLogs).toHaveBeenLastCalledWith("ingestion", "job-1", { after: "1-0", limit: 200 }, { signal: expect.any(AbortSignal) });
    expect(screen.getAllByText("Fetching feed")).toHaveLength(1);
    expect(screen.getByText("Ingested 10 entries")).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain("2 log entries");
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("drains pages even for completed jobs then stops after settling polls", async () => {
    vi.mocked(adminJobLogs)
      .mockResolvedValueOnce(page({ items: [entry("1-0")], next_cursor: "1-0", has_more: true, job_status: "succeeded" }))
      .mockResolvedValueOnce(page({ items: [entry("2-0", "Finished")], next_cursor: "2-0", job_status: "succeeded" }))
      .mockResolvedValue(page({ next_cursor: "2-0", job_status: "succeeded" }));
    await mount(); await advance(1); await advance(20000);
    expect(screen.getByText("Finished")).toBeDefined();
    const calls = vi.mocked(adminJobLogs).mock.calls.length;
    await advance(12000);
    expect(adminJobLogs).toHaveBeenCalledTimes(calls);
    expect(screen.queryByRole("button", { name: "Refresh logs" })).toBeNull();
  });

  it("keeps existing logs during an outage and recovers on retry", async () => {
    vi.mocked(adminJobLogs).mockResolvedValueOnce(page({ items: [entry("1-0")], next_cursor: "1-0" })).mockRejectedValueOnce(new ApiError(503));
    await mount(); await advance();
    expect(screen.getByRole("alert").textContent).toContain("Could not load runtime logs");
    expect(screen.getByText("Fetching feed")).toBeDefined();
    vi.mocked(adminJobLogs).mockResolvedValue(page({ items: [entry("2-0", "Retry succeeded")], next_cursor: "2-0" }));
    await advance(10000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Retry succeeded")).toBeDefined();
  });

  it("explains unavailable history without presenting it as a storage error", async () => {
    vi.mocked(adminJobLogs).mockResolvedValue(page({ job_status: "succeeded" }));
    await mount();
    expect(screen.getByText(/Historical console logs cannot be recovered/)).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("notifies once per log-storage outage, not on every retry", async () => {
    vi.mocked(adminJobLogs).mockRejectedValue(new ApiError(503));
    await mount(); await advance(30000);
    expect(toast.error).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await advance(1);
    expect(toast.error).toHaveBeenCalledTimes(1);
    vi.mocked(adminJobLogs).mockResolvedValue(page());
    await advance(10000);
    vi.mocked(adminJobLogs).mockRejectedValue(new ApiError(503));
    await advance(10000);
    expect(toast.error).toHaveBeenCalledTimes(2);
  });

  it("filters entries locally, shows attempts and renders potentially hostile strings as text", async () => {
    vi.mocked(adminJobLogs).mockResolvedValue(page({ items: [entry("1-0"), { ...entry("2-0", '<img src=x onerror="alert(1)">'), level: "ERROR", fields: { attempt: 2, error_type: "ValueError" } }] }));
    await mount();
    expect(screen.getByText("Attempt 2")).toBeDefined();
    expect(document.querySelector("img")).toBeNull();
    fireEvent.click(screen.getByRole("combobox", { name: "Log level" }));
    fireEvent.click(screen.getByRole("option", { name: "Warnings and errors" }));
    expect(screen.queryByText("Fetching feed")).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeDefined();
    fireEvent.change(screen.getByLabelText("Search logs"), { target: { value: "no match" } });
    expect(screen.getByText("No entries match these filters.")).toBeDefined();
    expect(adminJobLogs).toHaveBeenCalledTimes(1);
  });

  it("bounds loaded entries and reports retention trimming and corrupt records", async () => {
    vi.mocked(adminJobLogs).mockResolvedValue(page({ items: [entry("1-0", "Old"), entry("2-0", "Recent")], max_entries: 1, truncated: true, unreadable_entries: 1 }));
    await mount();
    expect(screen.queryByText("Old")).toBeNull();
    expect(screen.getByText("Recent")).toBeDefined();
    expect(screen.getByText(/Older entries were removed/).textContent).toContain("1 unreadable entries");
  });

  it("aborts requests and discards old logs on navigation", async () => {
    vi.mocked(adminJobLogs).mockResolvedValueOnce(page({ items: [entry("1-0")] }));
    const view = render(<JobLogs kind="ingestion" id="job-1" />);
    await advance(1);
    const oldSignal = vi.mocked(adminJobLogs).mock.calls[0][3]!.signal!;
    await act(async () => { view.rerender(<JobLogs kind="images" id="job-2" />); });
    expect(oldSignal.aborted).toBe(true);
    expect(screen.queryByText("Fetching feed")).toBeNull();
    expect(adminJobLogs).toHaveBeenLastCalledWith("images", "job-2", { after: undefined, limit: 200 }, { signal: expect.any(AbortSignal) });
    const count = vi.mocked(adminJobLogs).mock.calls.length;
    view.unmount(); await advance(10000);
    expect(adminJobLogs).toHaveBeenCalledTimes(count);
  });

  it("sends expired sessions to sign in without repeated polling", async () => {
    vi.mocked(adminJobLogs).mockRejectedValue(new ApiError(401));
    await mount(); await advance(10000);
    expect(returnToLogin).toHaveBeenCalledOnce();
    expect(adminJobLogs).toHaveBeenCalledOnce();
  });

  it("pauses updates from the saved setting without a page refresh control", async () => {
    await act(async () => { render(<RefreshSettings><JobLogs kind="ingestion" id="job-1" /></RefreshSettings>); });
    expect(screen.queryByRole("combobox", { name: "Log refresh interval" })).toBeNull();
    await saveRefresh(0);
    await advance(1);
    const count = vi.mocked(adminJobLogs).mock.calls.length;
    await advance(10000);
    expect(adminJobLogs).toHaveBeenCalledTimes(count);
  });
});
