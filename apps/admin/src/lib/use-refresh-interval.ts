"use client";

import { useSettings } from "./use-settings";

/** Every page reads the interval saved in Settings > Defaults. */
export function useRefreshInterval() {
  return useSettings().settings.defaults.refresh_seconds;
}

/** Processing views stay current even when general table refresh is disabled. */
export function useArticlePipelineInterval() {
  return Math.max(5, useRefreshInterval() || 5);
}
