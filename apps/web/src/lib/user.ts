import { trackUserMutation } from "./analytics";
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
  username?: string | null;
  bio?: string | null;
  location?: string | null;
  about?: string | null;
  links?: ProfileLink[];
  stack?: UserStack[];
  visibility?: ProfileVisibility;
  reading_streak?: ReadingStreak;
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

export class AccountError extends Error {
  constructor(public status: number) {
    super("Account request failed");
  }
}
export async function userRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await readerRequest(`/api/v1/user/${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    signal: init?.signal ?? AbortSignal.timeout(15000),
  });
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined")
      window.dispatchEvent(new Event("devfeed:user-session-expired"));
    throw new AccountError(response.status);
  }
  if (init?.method === "PUT" && (path.startsWith("preferences") || path === "settings/feed"))
    window.dispatchEvent(new Event("devfeed:interests-changed"));
  const result = response.status === 204 ? undefined : await response.json();
  trackUserMutation(path, init?.method, result, init?.body);
  return result as T;
}
