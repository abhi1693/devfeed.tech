"use client";

import { useEffect, useState } from "react";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import type { ReadModelSnapshot } from "@/lib/api/generated/models";
import { DateTime } from "./date-time";

export function SnapshotStatus({
  snapshot,
  checkedAt,
  maxAgeSeconds = 60,
  active = true,
}: {
  snapshot?: ReadModelSnapshot | null;
  checkedAt?: string | null;
  maxAgeSeconds?: number;
  active?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  const hasSnapshot = !!snapshot;
  useEffect(
    () =>
      active && hasSnapshot
        ? runWhenPageActive(
            () => {
              const timer = setInterval(() => setNow(Date.now()), 30_000);
              return () => clearInterval(timer);
            },
            { requireFocus: false },
          )
        : undefined,
    [active, hasSnapshot],
  );
  if (!snapshot) return null;
  const verified = Date.parse(checkedAt ?? "");
  const reference = Number.isFinite(verified) ? verified : Date.parse(snapshot.generated_at ?? "");
  const old = now - reference > maxAgeSeconds * 1000;
  const status = snapshot.status === "fresh" && old ? "stale" : snapshot.status;
  const label = {
    fresh: "Updated",
    stale: "Stale data · Last updated",
    refreshing: "Updating saved data · Last updated",
    rebuilding: "Rebuilding saved data · Last updated",
  }[status];
  return (
    <p
      role="status"
      className={`text-xs ${status === "fresh" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-300"}`}
    >
      {label}{" "}
      {snapshot.generated_at ? <DateTime value={snapshot.generated_at} /> : "not yet available"}
    </p>
  );
}
