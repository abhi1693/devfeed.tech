"use client";

import { useEffect } from "react";
import { startReaderAcquisition } from "@/lib/reader-acquisition";

/** Capture the first coarse landing before reader navigation without loading analytics. */
export function ReaderAcquisition({
  enabled,
  gaId,
  campaigns,
  extensionEnabled = false,
}: {
  enabled: boolean;
  gaId: string;
  campaigns: string[];
  extensionEnabled?: boolean;
}) {
  useEffect(
    () =>
      startReaderAcquisition({
        enabled: enabled && Boolean(gaId),
        gaId,
        campaigns,
        extensionEnabled: enabled && extensionEnabled,
      }),
    [enabled, gaId, campaigns, extensionEnabled],
  );
  return null;
}
