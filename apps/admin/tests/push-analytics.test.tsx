// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PushAnalyticsOverview } from "@/components/organisms/push-analytics";
import { adminPushAnalytics } from "@/lib/api/generated/admin";
import type { PushAnalytics } from "@/lib/api/generated/models";
import { emptyPushAnalyticsFixture, pushAnalyticsFixture } from "./fixtures/push-analytics";

vi.mock("@/lib/api/generated/admin", () => ({ adminPushAnalytics: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(adminPushAnalytics).mockResolvedValue(pushAnalyticsFixture());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("shows a named loading state until aggregate data arrives", async () => {
  let finish!: (data: PushAnalytics) => void;
  vi.mocked(adminPushAnalytics).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  render(<PushAnalyticsOverview />);
  expect(screen.getByRole("heading", { name: "Push analytics" })).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Loading push analytics…");
  await waitFor(() =>
    expect(adminPushAnalytics).toHaveBeenCalledWith(
      { days: 30 },
      { signal: expect.any(AbortSignal) },
    ),
  );
  await act(async () => finish(pushAnalyticsFixture()));
  expect(screen.queryByText("Loading push analytics…")).toBeNull();
  expect(screen.getByRole("table", { name: "Notification outcomes by type" })).toBeTruthy();
});

it("distinguishes relay acceptance, browser reports and reading using UTC publication cohorts", async () => {
  render(<PushAnalyticsOverview />);
  const table = await screen.findByRole("table", { name: "Notification outcomes by type" });
  const row = within(table).getByRole("row", { name: /Daily Must Read/ });
  expect(row.textContent).toContain("50.0%");
  expect(
    screen.getByRole("figure", { name: "Notification outcomes by UTC publication date" }),
  ).toBeTruthy();
  expect(screen.getByText(/Later outcomes update that same cohort/)).toBeTruthy();
  expect(screen.getByText(/missing reports remain unconfirmed/)).toBeTruthy();
  expect(screen.getByText(/An open means navigation succeeded/)).toBeTruthy();
  expect(screen.getByText("Relay accepted", { selector: "p" })).toBeTruthy();
  expect(screen.getByText("Reported displays", { selector: "p" })).toBeTruthy();
  expect(screen.queryByText("Delivered", { exact: true })).toBeNull();
});

it("leaves missing browser evidence unconfirmed and omits rate without reported displays", async () => {
  const data = pushAnalyticsFixture();
  data.totals = { ...data.totals, displayed: 0, clicked: 3, opened: 2, click_rate: 0 };
  data.by_kind = [{ kind: "daily_must_read", ...data.totals }];
  vi.mocked(adminPushAnalytics).mockResolvedValue(data);
  render(<PushAnalyticsOverview />);
  await screen.findByText("No reported displays to calculate a click rate.");
  expect(screen.getAllByText("—")).toHaveLength(2);
  expect(screen.queryByText("0.0%")).toBeNull();
  const table = screen.getByRole("table", { name: "Notification outcomes by type" });
  const cells = within(within(table).getByRole("row", { name: /Daily Must Read/ })).getAllByRole(
    "cell",
  );
  expect(cells.slice(3, 6).map((cell) => cell.textContent)).toEqual(["0", "3", "2"]);
});

it("shows empty and disabled states while preserving historical statistics", async () => {
  vi.mocked(adminPushAnalytics).mockResolvedValue({
    ...emptyPushAnalyticsFixture(),
    enabled: false,
  });
  render(<PushAnalyticsOverview />);
  await screen.findByText("Browser push is disabled.");
  expect(screen.getByText("Historical outcomes remain available here.")).toBeTruthy();
  expect(screen.getByText("No browser deliveries in this period.")).toBeTruthy();
  expect(screen.getByText("No notification events published in this period.")).toBeTruthy();
  expect(screen.queryByRole("figure")).toBeNull();
});

it("retries an initial failure without exposing backend error details", async () => {
  vi.mocked(adminPushAnalytics).mockRejectedValueOnce(
    new Error("private endpoint and signing key"),
  );
  render(<PushAnalyticsOverview />);
  await screen.findByRole("alert");
  expect(screen.queryByText(/private endpoint/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("table", { name: "Notification outcomes by type" });
  expect(adminPushAnalytics).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("cancels the previous window request and ignores its late results", async () => {
  let finish!: (data: PushAnalytics) => void;
  let oldSignal!: AbortSignal;
  vi.mocked(adminPushAnalytics).mockImplementation((params, options) =>
    params?.days === 30
      ? new Promise((resolve) => {
          finish = resolve;
          oldSignal = options!.signal as AbortSignal;
        })
      : Promise.resolve(emptyPushAnalyticsFixture(params?.days)),
  );
  render(<PushAnalyticsOverview />);
  await waitFor(() => expect(adminPushAnalytics).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  await screen.findByText("No notification events published in this period.");
  expect(oldSignal.aborted).toBe(true);
  await act(async () => finish(pushAnalyticsFixture()));
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "90 days" }));
  await waitFor(() =>
    expect(adminPushAnalytics).toHaveBeenLastCalledWith(
      { days: 90 },
      { signal: expect.any(AbortSignal) },
    ),
  );
});

it("keeps the last result through polling failures and recovers on retry", async () => {
  vi.useFakeTimers();
  await act(async () => {
    render(<PushAnalyticsOverview />);
  });
  expect(screen.getByRole("table", { name: "Notification outcomes by type" })).toBeTruthy();
  vi.mocked(adminPushAnalytics).mockRejectedValueOnce(new Error("temporary failure"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(screen.getByRole("alert").textContent).toContain("Showing the last successful result.");
  expect(screen.getByRole("table", { name: "Notification outcomes by type" })).toBeTruthy();
  expect(
    screen.getByRole("status", { name: "Push analytics refresh status" }).textContent,
  ).toContain("Stale data");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  });
  expect(screen.queryByRole("alert")).toBeNull();
});

it("shows stale generated data and never renders recipient or subscription identifiers", async () => {
  const data = {
    ...pushAnalyticsFixture(),
    generated_at: new Date(Date.now() - 600_000).toISOString(),
    endpoint: "private-relay-endpoint",
    user_id: "private-recipient-id",
    auth: "private-subscription-key",
  };
  vi.mocked(adminPushAnalytics).mockResolvedValue(data);
  render(<PushAnalyticsOverview />);
  await screen.findByRole("table", { name: "Notification outcomes by type" });
  expect(
    screen.getByRole("status", { name: "Push analytics refresh status" }).textContent,
  ).toContain("Stale data");
  expect(document.body.textContent).not.toContain("private-");
});
