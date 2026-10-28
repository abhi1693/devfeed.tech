export type LeaderboardEntry = {
  rank: number;
  position: number;
  username: string;
  display_name: string | null;
  avatar_url: string | null;
  days: number;
};

export type ReadingLeaderboard = {
  longest_streak: LeaderboardEntry[];
  reading_days: LeaderboardEntry[];
};

export type MyReadingRanks = {
  longest_streak: LeaderboardEntry | null;
  reading_days: LeaderboardEntry | null;
};

export function leaderboardName(entry: {
  display_name?: string | null;
  username?: string | null;
}): string {
  return entry.display_name?.trim() || entry.username?.trim() || "";
}
