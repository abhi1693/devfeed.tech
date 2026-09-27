// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReadingStreak } from "@/components/reading-streak";
import type { UserProfile } from "@/lib/user";
const account = vi.hoisted(() => ({
  user: { user_id: "reader" } as { user_id: string } | null,
  profile: null as UserProfile | null,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-28T12:00:00Z"));
  account.user = { user_id: "reader" };
  account.profile = {
    display_name: "Reader",
    avatar_url: null,
    reading_streak: {
      current_days: 3,
      longest_days: 8,
      total_days: 24,
      last_read_date: "2026-09-28",
    },
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("shows profile totals and today's status without requesting more data", () => {
  const fetcher = vi.spyOn(globalThis, "fetch");
  render(<ReadingStreak />);
  fireEvent.click(screen.getByRole("button", { name: "Reading streak: 3 days" }));
  expect(screen.getByRole("dialog", { name: "Your reading rhythm" })).toBeTruthy();
  expect(screen.getByText("Today counted")).toBeTruthy();
  expect(screen.getByText("Best streak").nextElementSibling?.textContent).toBe("8 days");
  expect(screen.getByText("Reading days").nextElementSibling?.textContent).toBe("24 total");
  expect(fetcher).not.toHaveBeenCalled();
  fetcher.mockRestore();
});
it.each([null, "2026-09-26"])(
  "shows a fresh start when the last reading day is %s",
  (last_read_date) => {
    account.profile!.reading_streak!.last_read_date = last_read_date;
    render(<ReadingStreak />);
    fireEvent.click(screen.getByRole("button", { name: "Reading streak: 0 days" }));
    expect(screen.getByText("Start with an article today.")).toBeTruthy();
  },
);
it("keeps yesterday's streak and updates at the UTC boundary", () => {
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  const view = render(<ReadingStreak />);
  fireEvent.click(screen.getByRole("button", { name: "Reading streak: 3 days" }));
  expect(screen.getByText("Keep it going. Open an article today.")).toBeTruthy();
  act(() => vi.advanceTimersByTime(12 * 60 * 60 * 1000 + 20));
  expect(screen.getByRole("button", { name: "Reading streak: 0 days" })).toBeTruthy();
  view.unmount();
  act(() => vi.advanceTimersByTime(0)); // Radix returns focus after closing.
  expect(vi.getTimerCount()).toBe(0);
});
it("hides unavailable data and removes the popover on sign out", () => {
  const view = render(<ReadingStreak />);
  fireEvent.click(screen.getByRole("button", { name: "Reading streak: 3 days" }));
  account.user = null;
  view.rerender(<ReadingStreak />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  account.user = { user_id: "reader" };
  account.profile = null;
  view.rerender(<ReadingStreak />);
  expect(screen.queryByRole("button")).toBeNull();
});
