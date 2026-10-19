import type { AccountOutTier } from "./api/generated/models";

const intervals: Record<AccountOutTier, number[]> = {
  bronze: [1440],
  silver: [1440],
  gold: [720, 1440],
  platinum: [360, 720, 1440],
  diamond: [60, 180, 360, 720, 1440],
};

export function syncIntervals(tier: AccountOutTier = "bronze") {
  return intervals[tier];
}
