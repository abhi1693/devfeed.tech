export type DateTimePreferences = {
  timezone: string;
  time_format: "system" | "12" | "24";
  date_format: "locale" | "iso" | "day-first" | "month-first";
};
export const defaultDateTimePreferences: DateTimePreferences = {
  timezone: "local",
  time_format: "system",
  date_format: "locale",
};

export function formatDate(
  value: string | number | Date,
  appearance: DateTimePreferences,
  dateOnly = false,
): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(dateOnly ? {} : { hour: "2-digit", minute: "2-digit" }),
  };
  if (appearance.timezone !== "local") options.timeZone = appearance.timezone;
  if (appearance.time_format !== "system") options.hour12 = appearance.time_format === "12";
  if (appearance.date_format === "locale") return date.toLocaleString(undefined, options);
  const parts = new Intl.DateTimeFormat("en-GB", {
    ...options,
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((value) => value.type === type)?.value ?? "";
  const day = part("day"),
    month = part("month"),
    year = part("year");
  const formatted =
    appearance.date_format === "iso"
      ? `${year}-${month}-${day}`
      : appearance.date_format === "day-first"
        ? `${day}/${month}/${year}`
        : `${month}/${day}/${year}`;
  return (
    formatted +
    (dateOnly
      ? ""
      : `, ${part("hour")}:${part("minute")}${part("dayPeriod") ? ` ${part("dayPeriod")}` : ""}`)
  );
}

export function timezoneOptions(current: string) {
  const zones = new Set([...Intl.supportedValuesOf("timeZone"), "Asia/Kolkata"]);
  if (current !== "local" && current !== "UTC") zones.add(current);
  return [
    { value: "local", label: "Device timezone" },
    { value: "UTC", label: "UTC" },
    ...Array.from(zones)
      .sort()
      .map((value) => ({ value, label: value.replaceAll("_", " ") })),
  ];
}
