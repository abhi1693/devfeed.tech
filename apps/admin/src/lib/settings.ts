import type {
  AppearanceSettings,
  DefaultSettings,
  NotificationSettings,
  ProfileSettings,
  TableSettings,
  UserSettings,
} from "./api/generated/models";

export type Settings = {
  profile: Required<ProfileSettings>;
  notifications: Required<NotificationSettings>;
  appearance: Required<AppearanceSettings>;
  defaults: Required<DefaultSettings>;
  tables: Record<string, TableSettings>;
};
export type SettingsSection = "profile" | "notifications" | "appearance" | "defaults";
export const settingsSections: { id: SettingsSection; label: string; description: string }[] = [
  {
    id: "profile",
    label: "Profile",
    description: "Personalize how your account appears in DevFeed.",
  },
  {
    id: "notifications",
    label: "Notifications",
    description: "Choose the updates you want in your inbox.",
  },
  { id: "appearance", label: "Appearance", description: "Make the admin app comfortable to use." },
  {
    id: "defaults",
    label: "Defaults",
    description: "Set your starting preferences for pages and tables.",
  },
];
export const defaultSettings: Settings = {
  profile: { display_name: null, avatar_url: null },
  notifications: { show_badge: true, sound: false },
  appearance: {
    theme: "system",
    density: "comfortable",
    reduce_motion: false,
    timezone: "local",
    time_format: "system",
    date_format: "locale",
  },
  defaults: {
    refresh_seconds: 10,
    page_size: 25,
    remember_columns: true,
    remember_filters: true,
    remember_sort: true,
    landing_page: "/",
    overview_days: 30,
  },
  tables: {},
};
export function normalizeSettings(value: UserSettings): Settings {
  return {
    profile: { ...defaultSettings.profile, ...value.profile },
    notifications: { ...defaultSettings.notifications, ...value.notifications },
    appearance: { ...defaultSettings.appearance, ...value.appearance },
    defaults: { ...defaultSettings.defaults, ...value.defaults },
    tables: value.tables ?? {},
  };
}

export { formatDate } from "@devfeed/ui/date-format";
