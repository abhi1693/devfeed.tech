import type { CardTheme, CardAccent } from "@devfeed/theme/dev-card";
import type { DateTimePreferences } from "@devfeed/ui/date-format";
import type { ThemeMode } from "@devfeed/ui/theme-toggle";
import type { contentTypes } from "./feed-query";
import { readerRequest } from "./reader-runtime";

export type UserIdentity = {
  user_id: string;
  name: string | null;
  email: string | null;
  expires_at: number;
  csrf_token: string;
};
export type UserProfile = {
  display_name: string | null;
  avatar_url: string | null;
  avatar_variants?: AvatarVariant[];
  username?: string | null;
  bio?: string | null;
  location?: string | null;
  about?: string | null;
  experience_level?: ExperienceLevel | null;
  links?: ProfileLink[];
  stack?: UserStack[];
  visibility?: ProfileVisibility;
  reading_streak?: ReadingStreak;
  dev_card?: DevCardSettings;
};
export type ExperienceLevel = "aspiring" | "entry_level" | "mid_level" | "senior";
export type AvatarVariant = { width: 32 | 64 | 128 | 256 | 512; url: string };
export type DevCardStat = "current_streak" | "longest_streak" | "total_reading_days";
export type DevCardSettings = {
  motion?: "static" | "animated";
  theme?: CardTheme;
  accent?: CardAccent;
  technologies?: string[] | null;
  stats: DevCardStat[];
};
export type ProfileLink = { url: string; label: string | null };
export type UserStack = {
  topic_id: string;
  section: "primary" | "hobby" | "learning" | "past";
  since_year: number | null;
  name: string;
  slug: string;
  kind: string;
  logo_url: string | null;
  logo_variants?: { url: string; width: number }[];
  status: string;
};
export type ProfileVisibility = {
  public: boolean;
  location: boolean;
  stack: boolean;
  heatmap: boolean;
  achievements: boolean;
};
export type ReadingStreak = {
  current_days: number;
  longest_days: number;
  total_days: number;
  last_read_date: string | null;
};
export type Preferences = { topic_ids: string[] };
export type FeedDisplay = {
  view: "cards" | "compact";
  languages: string[];
  content_types: (typeof contentTypes)[number][];
};
export type Appearance = DateTimePreferences & { theme: ThemeMode };
export type NotificationDisplay = { show_badge: boolean; sound: boolean };

export class AccountError extends Error {
  constructor(public status: number) {
    super("Account request failed");
  }
}
export async function userRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const signal = init?.signal ?? AbortSignal.timeout(15000);
  signal.throwIfAborted();
  const response = await readerRequest(`/api/v1/user/${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  signal.throwIfAborted();
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined")
      window.dispatchEvent(new Event("devfeed:user-session-expired"));
    throw new AccountError(response.status);
  }
  const result = response.status === 204 ? undefined : await response.json();
  signal.throwIfAborted();
  return result as T;
}
