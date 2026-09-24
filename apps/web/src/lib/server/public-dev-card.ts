import "server-only";
import { cache } from "react";
import { userApiOrigin } from "./config";
import type { UserProfile } from "../user";

// Only the unauthenticated, visibility-filtered endpoint may populate public cards.
export const publicDevCard = cache(async (username: string): Promise<UserProfile | null> => {
  if (!/^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$/.test(username)) return null;
  const response = await fetch(
    `${userApiOrigin()}/v1/user/profiles/${encodeURIComponent(username)}`,
    { cache: "no-store", signal: AbortSignal.timeout(10000) },
  );
  if (response.status === 404 || response.status === 422) return null;
  if (!response.ok) throw new Error("Public card is temporarily unavailable");
  return response.json();
});

export type PublicReadingActivity = {
  year: number;
  timezone: string;
  days: { date: string; article_count: number }[];
};

export const publicReadingActivity = cache(
  async (username: string): Promise<PublicReadingActivity | null> => {
    if (!/^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$/.test(username)) return null;
    try {
      const response = await fetch(
        `${userApiOrigin()}/v1/user/profiles/${encodeURIComponent(username)}/reading-heatmap`,
        {
          cache: "no-store",
          signal: AbortSignal.timeout(10000),
        },
      );
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  },
);
