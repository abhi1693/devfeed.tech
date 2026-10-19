"use client";

import { useRefreshInterval } from "./use-refresh-interval";

/** Source processing remains live when ordinary table refresh is off. */
export function useSourcePipelineInterval() {
  return Math.max(5, useRefreshInterval() || 5);
}
