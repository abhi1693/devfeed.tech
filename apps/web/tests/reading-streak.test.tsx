// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReaderQueryProvider } from "@/components/reader-query-provider";
import { ReadingStreak } from "@/components/reading-streak";
import { openMustReadsEvent } from "@/lib/reading-streak";
import type { UserProfile } from "@/lib/user";

const account = vi.hoisted(() => ({
  user: { user_id: "reader" } as { user_id: string } | null,
  profile: null as UserProfile | null,
  sessionRevision: 1,
}));
const request = vi.hoisted(() => vi.fn());
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", async (original) => ({
  ...(await original<typeof import("@/lib/user")>()),
  userRequest: request,
}));

const week = {
  today: "2026-09-28",
  timezone: "UTC",
  days: [
    { date: "2026-09-22", article_count: 1 },
    { date: "2026-09-23", article_count: 0 },
    { date: "2026-09-24", article_count: 2 },
    { date: "2026-09-25", article_count: 0 },
    { date: "2026-09-26", article_count: 1 },
    { date: "2026-09-27", article_count: 1 },
    { date: "2026-09-28", article_count: 1 },
  ],
};

function App() {
  return (
    <ReaderQueryProvider>
      <ReadingStreak />
    </ReaderQueryProvider>
  );
}

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
}

function trigger(days = 3) {
  return screen.getByRole("button", {
    name: `Reading streak: ${days} ${days === 1 ? "day" : "days"}`,
  });
}

function open(days = 3) {
  fireEvent.click(trigger(days));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-28T12:00:00Z"));
  sessionStorage.clear();
  account.user = { user_id: "reader" };
  account.sessionRevision = 1;
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
  request.mockReset();
  request.mockResolvedValue(week);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("loads private reading history only when the panel opens", async () => {
  render(<App />);
  expect(request).not.toHaveBeenCalled();
  open();
  await flush();
  expect(screen.getByRole("dialog", { name: "Your reading streak" })).toBeTruthy();
  expect(trigger().getAttribute("data-read-today")).toBe("true");
  expect(screen.getByRole("heading", { name: "3 days in a row" })).toBeTruthy();
  expect(
    screen
      .getByRole("listitem", { name: "September 28, 2026: 1 article opened, today" })
      .getAttribute("data-state"),
  ).toBe("read");
  expect(request).toHaveBeenCalledTimes(1);
  expect(request.mock.calls[0][0]).toBe("settings/reading-week");
  expect(request.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  fireEvent.click(screen.getByRole("button", { name: "Close reading streak" }));
  await flush();
  open();
  await flush();
  expect(request).toHaveBeenCalledTimes(1);
});

it("recognizes a personal best and keeps the next milestone within reach", async () => {
  account.profile!.reading_streak = {
    current_days: 11,
    longest_days: 11,
    total_days: 24,
    last_read_date: "2026-09-28",
  };
  render(<App />);
  open(11);
  await flush();
  expect(screen.getByText("Personal best")).toBeTruthy();
  expect(screen.getByText(/3 days to go/)).toBeTruthy();
  expect(screen.queryByText("Best: 11 days")).toBeNull();
});

it("offers a useful next read while keeping yesterday's streak", async () => {
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  render(<App />);
  open();
  await flush();
  expect(trigger().getAttribute("data-read-today")).toBe("false");
  const listener = vi.fn();
  window.addEventListener(openMustReadsEvent, listener);
  fireEvent.click(screen.getByRole("button", { name: "Find today’s read" }));
  await flush();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(listener).toHaveBeenCalledTimes(1);
  window.removeEventListener(openMustReadsEvent, listener);
});

it("welcomes a first reading day without inventing an achievement", async () => {
  account.profile!.reading_streak = {
    current_days: 0,
    longest_days: 0,
    total_days: 0,
    last_read_date: null,
  };
  render(<App />);
  open(0);
  await flush();
  expect(screen.getByRole("heading", { name: "Start your streak" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Find today’s read" })).toBeTruthy();
  expect(screen.queryByText("Personal best")).toBeNull();
});

it("keeps a previous personal best visible after a missed day", async () => {
  account.profile!.reading_streak!.last_read_date = "2026-09-26";
  render(<App />);
  open(0);
  await flush();
  expect(screen.getByRole("heading", { name: "Start a new streak" })).toBeTruthy();
  expect(screen.getByText("Best: 8 days")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Find today’s read" })).toBeTruthy();
  expect(screen.queryByText("Personal best")).toBeNull();
});

it("updates the streak at the UTC boundary without waiting for another profile request", async () => {
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  const view = render(<App />);
  open();
  await flush();
  expect(trigger().getAttribute("data-read-today")).toBe("false");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000 + 20);
  });
  expect(screen.getByRole("button", { name: "Reading streak: 0 days" })).toBeTruthy();
  expect(screen.getByText("Best: 8 days")).toBeTruthy();
  view.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(vi.getTimerCount()).toBe(0);
});

it("aborts private history on sign out and ignores a late response", async () => {
  let resolve!: (value: typeof week) => void;
  request.mockImplementationOnce(() => new Promise<typeof week>((done) => (resolve = done)));
  const view = render(<App />);
  open();
  await flush();
  const signal = request.mock.calls[0][1].signal as AbortSignal;
  account.user = null;
  view.rerender(<App />);
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  await act(async () => resolve(week));
  expect(screen.queryByRole("dialog")).toBeNull();
  account.user = { user_id: "reader" };
  account.profile = null;
  view.rerender(<App />);
  expect(screen.queryByRole("button")).toBeNull();
});

it("closes a previous account's panel and aborts its request on account changes", async () => {
  request.mockImplementationOnce(() => new Promise(() => {}));
  const view = render(<App />);
  open();
  await flush();
  const signal = request.mock.calls[0][1].signal as AbortSignal;
  account.user = { user_id: "another-reader" };
  account.sessionRevision++;
  view.rerender(<App />);
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
  open();
  await flush();
  expect(request).toHaveBeenCalledTimes(2);
});

it("celebrates a confirmed new reading day once and avoids replaying it on reload", async () => {
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  const view = render(<App />);
  open();
  await flush();
  expect(screen.queryByText("Day 3 counted.")).toBeNull();
  account.profile = {
    ...account.profile!,
    reading_streak: {
      ...account.profile!.reading_streak!,
      current_days: 4,
      last_read_date: "2026-09-28",
    },
  };
  view.rerender(<App />);
  await flush();
  expect(screen.getByText("Day 4 counted.").closest('[role="status"]')).toBeTruthy();
  view.unmount();
  const reloaded = render(<App />);
  open(4);
  await flush();
  expect(screen.queryByText("Day 4 counted.")).toBeNull();
  // A stale profile followed by the same confirmed day must not replay the celebration.
  account.profile = {
    ...account.profile!,
    reading_streak: { ...account.profile!.reading_streak!, last_read_date: "2026-09-27" },
  };
  reloaded.rerender(<App />);
  account.profile = {
    ...account.profile!,
    reading_streak: { ...account.profile!.reading_streak!, last_read_date: "2026-09-28" },
  };
  reloaded.rerender(<App />);
  await flush();
  expect(screen.queryByText("Day 4 counted.")).toBeNull();
});

it("gives a confirmed milestone stronger recognition without confusing it with a daily count", async () => {
  account.profile!.reading_streak = {
    current_days: 6,
    longest_days: 10,
    total_days: 24,
    last_read_date: "2026-09-27",
  };
  const view = render(<App />);
  open(6);
  await flush();
  account.profile = {
    ...account.profile!,
    reading_streak: {
      ...account.profile!.reading_streak!,
      current_days: 7,
      last_read_date: "2026-09-28",
    },
  };
  view.rerender(<App />);
  await flush();
  expect(screen.getByText("7 days. Milestone earned!").closest('[role="status"]')).toBeTruthy();
  expect(screen.queryByText("Day 7 counted.")).toBeNull();
});

it("keeps the confirmed streak usable when session storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Storage unavailable", "QuotaExceededError");
  });
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  const view = render(<App />);
  open();
  await flush();
  account.profile = {
    ...account.profile!,
    reading_streak: {
      ...account.profile!.reading_streak!,
      current_days: 4,
      last_read_date: "2026-09-28",
    },
  };
  view.rerender(<App />);
  await flush();
  expect(screen.getByRole("button", { name: "Reading streak: 4 days" })).toBeTruthy();
  expect(trigger(4).getAttribute("data-read-today")).toBe("true");
});

it("shows genuine daily history and distinguishes today from days without opens", async () => {
  request.mockResolvedValue({
    ...week,
    days: week.days.map((day) => (day.date === week.today ? { ...day, article_count: 0 } : day)),
  });
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  render(<App />);
  open();
  await flush();
  const trail = within(screen.getByRole("region", { name: "Your week" }));
  expect(trail.getAllByRole("listitem")).toHaveLength(7);
  expect(
    trail
      .getByRole("listitem", { name: "September 24, 2026: 2 articles opened" })
      .getAttribute("data-state"),
  ).toBe("read");
  expect(
    trail
      .getByRole("listitem", { name: "September 25, 2026: 0 articles opened" })
      .getAttribute("data-state"),
  ).toBe("missed");
  expect(
    trail
      .getByRole("listitem", { name: "September 28, 2026: 0 articles opened, today" })
      .getAttribute("data-state"),
  ).toBe("pending");
});

it("shows a loading state instead of fabricating missed reading days", async () => {
  request.mockImplementationOnce(() => new Promise(() => {}));
  render(<App />);
  open();
  await flush();
  const trail = within(screen.getByRole("region", { name: "Your week" }));
  expect(trail.getByRole("status").textContent).toBe("Loading your week…");
  expect(trail.queryByRole("listitem")).toBeNull();
  expect(trigger().getAttribute("data-read-today")).toBe("true");
});

it("keeps the confirmed streak visible after a history failure and supports an explicit retry", async () => {
  request.mockRejectedValueOnce(new Error("Network unavailable"));
  render(<App />);
  open();
  await flush();
  const trail = within(screen.getByRole("region", { name: "Your week" }));
  expect(trail.getByText("Couldn’t load your reading week.")).toBeTruthy();
  expect(trail.queryByRole("listitem")).toBeNull();
  expect(trigger().getAttribute("data-read-today")).toBe("true");
  expect(request).toHaveBeenCalledTimes(1);
  request.mockResolvedValueOnce(week);
  fireEvent.click(trail.getByRole("button", { name: "Try again" }));
  await flush();
  expect(trail.getAllByRole("listitem")).toHaveLength(7);
  expect(request).toHaveBeenCalledTimes(2);
});

it.each([
  { ...week, days: week.days.slice(1) },
  { ...week, days: week.days.map((day, i) => (i === 0 ? { ...day, article_count: -1 } : day)) },
  { ...week, days: week.days.map((day, i) => (i === 0 ? { ...day, date: "2026-09-21" } : day)) },
])("does not display malformed history as missed days", async (invalidWeek) => {
  request.mockResolvedValueOnce(invalidWeek);
  render(<App />);
  open();
  await flush();
  const trail = within(screen.getByRole("region", { name: "Your week" }));
  expect(trail.getByText("Couldn’t load your reading week.")).toBeTruthy();
  expect(trail.queryByRole("listitem")).toBeNull();
});

it("announces a confirmed new personal best and clears the transient celebration", async () => {
  account.profile!.reading_streak = {
    current_days: 10,
    longest_days: 10,
    total_days: 24,
    last_read_date: "2026-09-27",
  };
  const view = render(<App />);
  account.profile = {
    ...account.profile!,
    reading_streak: {
      current_days: 11,
      longest_days: 11,
      total_days: 25,
      last_read_date: "2026-09-28",
    },
  };
  view.rerender(<App />);
  await flush();
  expect(screen.getByText("11 days. A new personal best!")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.queryByText("11 days. A new personal best!")).toBeNull();
  expect(screen.getByRole("button", { name: "Reading streak: 11 days" })).toBeTruthy();
});

it("refreshes an expired reading week when the panel reopens", async () => {
  render(<App />);
  open();
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Close reading streak" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(61_000);
  });
  expect(request).toHaveBeenCalledTimes(1);
  open();
  await flush();
  expect(request).toHaveBeenCalledTimes(2);
});

it("aborts the previous credentials' history when the session refreshes", async () => {
  request.mockImplementationOnce(() => new Promise(() => {}));
  const view = render(<App />);
  open();
  await flush();
  const signal = request.mock.calls[0][1].signal as AbortSignal;
  account.sessionRevision++;
  view.rerender(<App />);
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
});

it.each([120, 150])(
  "recognizes a confirmed %i-day milestone beyond the initial milestone set",
  async (days) => {
    account.profile!.reading_streak = {
      current_days: days - 1,
      longest_days: 200,
      total_days: 400,
      last_read_date: "2026-09-27",
    };
    const view = render(<App />);
    account.profile = {
      ...account.profile!,
      reading_streak: {
        ...account.profile!.reading_streak!,
        current_days: days,
        last_read_date: "2026-09-28",
      },
    };
    view.rerender(<App />);
    await flush();
    expect(screen.getByText(`${days} days. Milestone earned!`)).toBeTruthy();
    expect(screen.queryByText(`Day ${days} counted.`)).toBeNull();
  },
);

it.each(["another-reader", "reader", null])(
  "cancels a deferred Must Reads action when the account or session changes to %s",
  async (owner) => {
    account.profile!.reading_streak!.last_read_date = "2026-09-27";
    const view = render(<App />);
    open();
    await flush();
    const listener = vi.fn();
    window.addEventListener(openMustReadsEvent, listener);
    fireEvent.click(screen.getByRole("button", { name: "Find today’s read" }));
    account.user = owner ? { user_id: owner } : null;
    account.sessionRevision++;
    view.rerender(<App />);
    await flush();
    window.removeEventListener(openMustReadsEvent, listener);
    expect(listener).not.toHaveBeenCalled();
  },
);

it("ignores a previous day's late history response after the UTC boundary", async () => {
  vi.setSystemTime(new Date("2026-09-28T23:59:59.900Z"));
  let resolveOld!: (value: typeof week) => void;
  const tomorrow = {
    ...week,
    today: "2026-09-29",
    days: week.days.map((day) => ({
      ...day,
      date: new Date(Date.parse(day.date) + 86_400_000).toISOString().slice(0, 10),
    })),
  };
  request
    .mockImplementationOnce(() => new Promise<typeof week>((done) => (resolveOld = done)))
    .mockResolvedValue(tomorrow);
  render(<App />);
  open();
  await flush();
  const oldSignal = request.mock.calls[0][1].signal as AbortSignal;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(500);
  });
  expect(oldSignal.aborted).toBe(true);
  expect(request).toHaveBeenCalledTimes(2);
  await act(async () => resolveOld(week));
  await flush();
  const trail = within(screen.getByRole("region", { name: "Your week" }));
  expect(
    trail.getByRole("listitem", { name: "September 29, 2026: 1 article opened, today" }),
  ).toBeTruthy();
  expect(
    trail.queryByRole("listitem", { name: "September 28, 2026: 1 article opened, today" }),
  ).toBeNull();
});

it("opens today's picks once after close focus restoration under Strict Mode", async () => {
  account.profile!.reading_streak!.last_read_date = "2026-09-27";
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  const trigger = screen.getByRole("button", { name: "Reading streak: 3 days" });
  open();
  await flush();
  let focusedAtDispatch: Element | null = null;
  const listener = vi.fn(() => {
    focusedAtDispatch = document.activeElement;
  });
  window.addEventListener(openMustReadsEvent, listener);
  fireEvent.click(screen.getByRole("button", { name: "Find today’s read" }));
  await flush();
  window.removeEventListener(openMustReadsEvent, listener);
  expect(listener).toHaveBeenCalledTimes(1);
  expect(focusedAtDispatch).toBe(trigger);
});
