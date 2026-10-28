// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  DateRangePicker,
  dateRangeBounds,
  dateRangePresets,
  type DateRange,
} from "@devfeed/ui/date-range-picker";

const now = new Date("2026-10-26T18:00:00Z");
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(now);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it.each([
  [{ from: "now/d", to: "now" }, "2026-10-26", "2026-10-26"],
  [{ from: "now-2d/d", to: "now" }, "2026-10-24", "2026-10-26"],
  [{ from: "now-6d/d", to: "now" }, "2026-10-20", "2026-10-26"],
  [{ from: "now-90d", to: "now" }, "2026-07-28", "2026-10-26"],
  [{ from: "now-2y", to: "now" }, "2024-10-26", "2026-10-26"],
  [{ from: "now-3M", to: "now" }, "2026-07-26", "2026-10-26"],
  [{ from: "now-1d/d", to: "now-1d/d" }, "2026-10-25", "2026-10-25"],
  [{ from: "2024-11-01", to: "2024-11-16" }, "2024-11-01", "2024-11-16"],
  [{ to: "2024-03-01" }, "2024-02-01", "2024-03-01"],
  [{ from: "2026-10-01" }, "2026-10-01", "2026-10-26"],
  [{}, "2026-09-27", "2026-10-26"],
])("resolves %j to whole UTC days without display-label logic", (range, start, end) => {
  expect(dateRangeBounds(range, now)).toEqual({ start, end });
});
it("handles calendar years and a leap-year month boundary", () => {
  const leap = new Date("2024-03-31T23:30:00Z");
  expect(dateRangeBounds({ from: "now-1M", to: "now" }, leap)).toEqual({
    start: "2024-02-29",
    end: "2024-03-31",
  });
  const presets = dateRangePresets();
  expect(dateRangeBounds(presets.find(({ label }) => label === "This year")!.value, leap)).toEqual({
    start: "2024-01-01",
    end: "2024-03-31",
  });
  expect(dateRangeBounds(presets.find(({ label }) => label === "Last year")!.value, leap)).toEqual({
    start: "2023-01-01",
    end: "2023-12-31",
  });
  expect(dateRangeBounds({ from: "2024-01-01", to: "2024-12-31" }, now)).toEqual({
    start: "2024-01-01",
    end: "2024-12-31",
  });
});
it.each([
  "now-3670d",
  "now-999999y",
  "2024-02-31",
  "2027-01-01",
  "Last 7 days",
  "",
  "P1D",
  "now/invalid",
])("rejects an invalid or unbounded from value %s", (from) =>
  expect(dateRangeBounds({ from, to: "now" }, now)).toBeNull(),
);

function Example({ changed = vi.fn() }: { changed?: (value: DateRange) => void }) {
  const [value, setValue] = useState<DateRange>({ from: "2026-10-01", to: "2026-10-26" });
  return (
    <DateRangePicker
      value={value}
      onChange={(next) => {
        setValue(next);
        changed(next);
      }}
    />
  );
}
it("offers the requested day, month and calendar presets using standard API values", async () => {
  const changed = vi.fn();
  const user = userEvent.setup();
  render(<Example changed={changed} />);
  const trigger = screen.getByRole("button", { name: "Chart date range" });
  await user.click(trigger);
  for (const label of [
    "Today",
    "Yesterday",
    "Last 3 days",
    "Last 7 days",
    "Last 14 days",
    "Last 30 days",
    "Last 3 months",
    "Last 6 months",
    "Last 9 months",
    "This year",
    "Last year",
  ])
    expect(screen.getByRole("button", { name: label })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "90 days" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Last 6 months" }));
  expect(changed).toHaveBeenCalledExactlyOnceWith({ from: "now-6M", to: "now" });
  expect(trigger.textContent).toContain("Last 6 months");
  expect(screen.queryByRole("dialog", { name: "Choose time range" })).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});
it("applies a custom inclusive selection using shared calendars and inclusive from/to dates", async () => {
  const changed = vi.fn();
  render(<Example changed={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Chart date range" }));
  fireEvent.click(screen.getByRole("button", { name: /^From:/ }));
  fireEvent.click(screen.getByRole("button", { name: "Tuesday, October 20, 2026" }));
  fireEvent.click(screen.getByRole("button", { name: /^To:/ }));
  fireEvent.click(screen.getByRole("button", { name: "Sunday, October 25, 2026" }));
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Apply time range" }));
  expect(changed).toHaveBeenCalledExactlyOnceWith({ from: "2026-10-20", to: "2026-10-25" });
  expect(screen.getByRole("button", { name: "Chart date range" }).textContent).toContain(
    "2026-10-20 – 2026-10-25",
  );
});
it("keeps invalid drafts out of the current range and restores focus on Escape", async () => {
  const changed = vi.fn();
  const user = userEvent.setup();
  render(<Example changed={changed} />);
  const trigger = screen.getByRole("button", { name: "Chart date range" });
  await user.click(trigger);
  await user.click(screen.getByRole("button", { name: /^From:/ }));
  await user.click(screen.getByRole("button", { name: "Clear date" }));
  await user.click(screen.getByRole("button", { name: "Apply time range" }));
  expect(screen.getByRole("alert").textContent).toContain("Choose valid dates");
  expect(changed).not.toHaveBeenCalled();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Choose time range" })).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  expect(trigger.textContent).toContain("2026-10-01 – 2026-10-26");
});
