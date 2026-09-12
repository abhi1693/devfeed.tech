import { expect, it } from "vitest";
import { defaultDateTimePreferences, formatDate, timezoneOptions } from "@devfeed/ui/date-format";

it("uses the selected timezone across day boundaries and daylight saving changes", () => {
  const settings = {
    ...defaultDateTimePreferences,
    timezone: "America/New_York",
    date_format: "iso" as const,
    time_format: "24" as const,
  };
  expect(formatDate("2026-09-11T01:00:00Z", settings)).toBe("2026-09-10, 21:00");
  expect(formatDate("2026-03-08T06:30:00Z", settings)).toBe("2026-03-08, 01:30");
  expect(formatDate("2026-03-08T07:30:00Z", settings)).toBe("2026-03-08, 03:30");
  expect(formatDate("2026-09-11T01:00:00Z", { ...settings, date_format: "day-first" }, true)).toBe(
    "10/09/2026",
  );
  expect(
    formatDate("2026-09-11T01:00:00Z", {
      ...settings,
      date_format: "month-first",
      time_format: "12",
    }),
  ).toMatch(/^09\/10\/2026, 09:00 pm$/i);
  expect(formatDate("bad date", settings)).toBe("—");
  expect(
    timezoneOptions("Asia/Kolkata").filter((zone) => zone.value === "Asia/Kolkata"),
  ).toHaveLength(1);
});
