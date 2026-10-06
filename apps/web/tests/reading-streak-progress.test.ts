import { afterEach, expect, it, vi } from "vitest";
import {
  getStreakProgress,
  utcDay,
  validateReadingWeek,
  type ReadingWeek,
} from "@/lib/reading-streak";
import type { ReadingStreak } from "@/lib/user";

const today = "2026-09-28";
const streak = (days: number, extra: Partial<ReadingStreak> = {}): ReadingStreak => ({
  current_days: days,
  longest_days: days,
  total_days: days,
  last_read_date: today,
  ...extra,
});

afterEach(() => vi.useRealTimers());

it.each([
  [0, 3, 3],
  [1, 3, 2],
  [3, 7, 4],
  [7, 14, 7],
  [11, 14, 3],
  [14, 30, 16],
  [30, 60, 30],
  [60, 100, 40],
  [100, 120, 20],
  [120, 150, 30],
  [149, 150, 1],
  [150, 180, 30],
])("gives a %i-day streak a reachable next goal of %i days", (days, next, remaining) => {
  const progress = getStreakProgress(streak(days), today);
  expect(progress.current).toBe(days);
  expect(progress.next).toBe(next);
  expect(progress.remaining).toBe(remaining);
  expect(progress.progress).toBeGreaterThanOrEqual(0);
  expect(progress.progress).toBeLessThan(100);
});

it("preserves yesterday's streak across a year boundary", () => {
  expect(
    getStreakProgress(streak(11, { last_read_date: "2026-12-31" }), "2027-01-01"),
  ).toMatchObject({ current: 11, readToday: false, next: 14, remaining: 3 });
});

it.each([null, "2026-09-26", "2026-09-29"])(
  "uses fresh-start progress for an inactive or future reading date: %s",
  (last_read_date) => {
    expect(
      getStreakProgress(streak(11, { longest_days: 30, last_read_date }), today),
    ).toMatchObject({
      current: 0,
      readToday: false,
      personalBest: false,
      next: 3,
      remaining: 3,
      progress: 0,
      earned: [3, 7, 14, 30],
    });
  },
);

it("keeps earned milestones after a broken streak, including achievements beyond 100 days", () => {
  expect(
    getStreakProgress(streak(1, { longest_days: 150, total_days: 240 }), today).earned,
  ).toEqual([3, 7, 14, 30, 60, 100, 120, 150]);
});

it("recognizes a positive personal best and avoids awarding one for zero days", () => {
  expect(getStreakProgress(streak(11), today).personalBest).toBe(true);
  expect(getStreakProgress(streak(11, { longest_days: 30 }), today).personalBest).toBe(false);
  expect(getStreakProgress(streak(0, { last_read_date: null }), today).personalBest).toBe(false);
});

it("uses UTC dates regardless of the viewer's local offset", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-29T04:59:59+05:30"));
  expect(utcDay()).toBe("2026-09-28");
  vi.setSystemTime(new Date("2026-09-29T05:30:00+05:30"));
  expect(utcDay()).toBe("2026-09-29");
});

const week: ReadingWeek = {
  today,
  timezone: "UTC",
  days: Array.from({ length: 7 }, (_, index) => ({
    date: "2026-09-" + (22 + index),
    article_count: index === 6 ? 1 : 0,
  })),
};

it("accepts the complete ordered week without rewriting real reading counts", () => {
  expect(validateReadingWeek(week)).toEqual(week);
});

it.each([
  ["local timezone", { ...week, timezone: "Asia/Kolkata" }],
  ["invalid date", { ...week, today: "not-a-date" }],
  ["missing day", { ...week, days: week.days.slice(1) }],
  ["unordered days", { ...week, days: [...week.days].reverse() }],
  ["duplicate day", { ...week, days: week.days.map(() => week.days[0]) }],
  ["negative count", { ...week, days: week.days.map((day) => ({ ...day, article_count: -1 })) }],
  ["fractional count", { ...week, days: week.days.map((day) => ({ ...day, article_count: 0.5 })) }],
  [
    "unsafe count",
    {
      ...week,
      days: week.days.map((day) => ({ ...day, article_count: Number.MAX_SAFE_INTEGER + 1 })),
    },
  ],
])("rejects history with %s instead of displaying invented activity", (_, value) => {
  expect(() => validateReadingWeek(value as ReadingWeek)).toThrow("Invalid reading history");
});
