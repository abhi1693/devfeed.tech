import { trackUserMutation } from "./analytics";

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
};
export type Preferences = { topic_ids: string[] };

export class AccountError extends Error {
  constructor(public status: number) {
    super("Account request failed");
  }
}
export async function userRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/user/${path}`, {
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
  if (
    init?.method === "PUT" &&
    (path.startsWith("preferences") || path.endsWith("/like") || path === "settings/feed")
  )
    window.dispatchEvent(new Event("devfeed:interests-changed"));
  const result = response.status === 204 ? undefined : await response.json();
  trackUserMutation(path, init?.method, result, init?.body);
  return result as T;
}
