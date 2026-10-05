import type { ReadingStreak } from "./user";

export const readingMilestones = [3, 7, 14, 30, 60, 100] as const;
export const openMustReadsEvent = "devfeed:open-must-reads";

export type ReadingWeek = {
  today: string;
  timezone: "UTC";
  days: { date: string; article_count: number }[];
};

export function utcDay(moment = new Date()) {
  return moment.toISOString().slice(0, 10);
}

export function getStreakProgress(streak: ReadingStreak, today: string) {
  const yesterday = utcDay(new Date(Date.parse(today) - 86_400_000));
  const readToday = streak.last_read_date === today;
  const current = readToday || streak.last_read_date === yesterday ? streak.current_days : 0;
  const next =
    readingMilestones.find((day) => day > current) ?? (Math.floor(current / 30) + 1) * 30;
  const earned: number[] = readingMilestones.filter((day) => day <= streak.longest_days);
  for (let day = 120; day <= streak.longest_days; day += 30) earned.push(day);
  return {
    current,
    readToday,
    next,
    remaining: next - current,
    progress: Math.min(100, (current / next) * 100),
    personalBest: current > 0 && current >= streak.longest_days,
    earned,
  };
}

export function validateReadingWeek(value: ReadingWeek) {
  if (
    value.timezone !== "UTC" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(value.today) ||
    !Number.isFinite(Date.parse(value.today)) ||
    !Array.isArray(value.days) ||
    value.days.length !== 7
  )
    throw new Error("Invalid reading history");
  const today = Date.parse(value.today);
  for (const [index, day] of value.days.entries()) {
    if (
      day.date !== utcDay(new Date(today - (6 - index) * 86_400_000)) ||
      !Number.isSafeInteger(day.article_count) ||
      day.article_count < 0
    )
      throw new Error("Invalid reading history");
  }
  return value;
}
