import type { AppearanceSettings, DefaultSettings, NotificationSettings, ProfileSettings, TableSettings, UserSettings } from "./api/generated/models";

export type Settings = {
  profile: Required<ProfileSettings>;
  notifications: Required<NotificationSettings>;
  appearance: Required<AppearanceSettings>;
  defaults: Required<DefaultSettings>;
  tables: Record<string, TableSettings>;
};
export type SettingsSection = "profile" | "notifications" | "appearance" | "defaults";
export const settingsSections: { id: SettingsSection; label: string; description: string }[] = [
  { id: "profile", label: "Profile", description: "Personalize how your account appears in DevFeed." },
  { id: "notifications", label: "Notifications", description: "Choose the updates you want in your inbox." },
  { id: "appearance", label: "Appearance", description: "Make the admin app comfortable to use." },
  { id: "defaults", label: "Defaults", description: "Set your starting preferences for pages and tables." },
];
export const defaultSettings: Settings = {
  profile: { display_name: null, avatar_url: null },
  notifications: { show_badge: true, sound: false },
  appearance: { theme: "system", density: "comfortable", reduce_motion: false, timezone: "local", time_format: "system", date_format: "locale" },
  defaults: { refresh_seconds: 10, page_size: 25, remember_columns: true, remember_filters: true, remember_sort: true, landing_page: "/", overview_days: 30 },
  tables: {},
};
export function normalizeSettings(value: UserSettings): Settings {
  return { profile: { ...defaultSettings.profile, ...value.profile }, notifications: { ...defaultSettings.notifications, ...value.notifications },
    appearance: { ...defaultSettings.appearance, ...value.appearance }, defaults: { ...defaultSettings.defaults, ...value.defaults }, tables: value.tables ?? {} };
}

export function formatDate(value: string | number | Date, appearance: Settings["appearance"], dateOnly = false): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  const options: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric", ...(dateOnly ? {} : { hour: "2-digit", minute: "2-digit" }) };
  if (appearance.timezone !== "local") options.timeZone = appearance.timezone;
  if (appearance.time_format !== "system") options.hour12 = appearance.time_format === "12";
  if (appearance.date_format === "locale") return date.toLocaleString(undefined, options);
  const parts = new Intl.DateTimeFormat("en-GB", { ...options, month: "2-digit", day: "2-digit" }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(value => value.type === type)?.value ?? "";
  const day = part("day"), month = part("month"), year = part("year");
  const formatted = appearance.date_format === "iso" ? `${year}-${month}-${day}` : appearance.date_format === "day-first" ? `${day}/${month}/${year}` : `${month}/${day}/${year}`;
  return formatted + (dateOnly ? "" : `, ${part("hour")}:${part("minute")}${part("dayPeriod") ? ` ${part("dayPeriod")}` : ""}`);
}
