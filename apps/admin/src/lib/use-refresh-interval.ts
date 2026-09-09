"use client";

import { useSettings } from "./use-settings";

/** Every page reads the interval saved in Settings > Defaults. */
export function useRefreshInterval() {
  return useSettings().settings.defaults.refresh_seconds;
}
