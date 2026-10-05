import { afterEach, expect, it, vi } from "vitest";
import { defaultDateTimePreferences, formatDate, timezoneOptions } from "@devfeed/ui/date-format";

afterEach(() => vi.restoreAllMocks());

it("keeps device and UTC choices first and preserves the current timezone without duplicates", () => {
  vi.spyOn(Intl, "supportedValuesOf").mockReturnValue([
    "Europe/Paris",
    "Australia/Sydney",
    "Asia/Kolkata",
    "Europe/Paris",
  ]);
  expect(timezoneOptions("America/New_York")).toEqual([
    { value: "local", label: "Device timezone" },
    { value: "UTC", label: "UTC" },
    { value: "America/New_York", label: "America/New York" },
    { value: "Asia/Kolkata", label: "Asia/Kolkata" },
    { value: "Australia/Sydney", label: "Australia/Sydney" },
    { value: "Europe/Paris", label: "Europe/Paris" },
  ]);
});

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
