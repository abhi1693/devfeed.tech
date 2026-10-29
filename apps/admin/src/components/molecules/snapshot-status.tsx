"use client";

import { useEffect, useState } from "react";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import type { AnalyticsSnapshot } from "@/lib/api/generated/models";
import { DateTime } from "./date-time";

export function SnapshotStatus({
  snapshot,
  requireCoverage = false,
  maxAgeSeconds = 60,
  active = true,
}: {
  snapshot?: AnalyticsSnapshot | null;
  requireCoverage?: boolean;
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
              const deadline = Date.parse(snapshot?.coverage_expires_at ?? "");
              const expiryTimer = Number.isFinite(deadline)
                ? setTimeout(() => setNow(Date.now()), Math.max(0, deadline - Date.now()))
                : undefined;
              return () => {
                clearInterval(timer);
                clearTimeout(expiryTimer);
              };
            },
            { requireFocus: false },
          )
        : undefined,
    [active, hasSnapshot, snapshot?.coverage_expires_at],
  );
  if (!snapshot) return null;
  const reference = Date.parse(snapshot.generated_at ?? "");
  const expiresAt = Date.parse(snapshot.coverage_expires_at ?? "");
  const coverage = requireCoverage || snapshot.coverage_expires_at != null;
  const old =
    coverage && Number.isFinite(expiresAt)
      ? now >= expiresAt
      : now - reference > maxAgeSeconds * 1000;
  const verified = Number.isFinite(reference) && Number.isFinite(expiresAt);
  const status =
    (snapshot.status === "fresh" || snapshot.status === "catching_up") && old
      ? "stale"
      : coverage && snapshot.status === "fresh" && !verified
        ? "catching_up"
        : snapshot.status;
  const label = {
    fresh: coverage ? "Verified through" : "Updated",
    stale: coverage ? "Stale data · Verified through" : "Stale data · Last updated",
    catching_up: coverage ? "Catching up · Verified through" : "Catching up · Last updated",
  }[status];
  return (
    <p
      role="status"
      className={`text-xs ${status === "fresh" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-300"}`}
    >
      {Number.isFinite(reference) && snapshot.generated_at ? (
        <>
          {label} <DateTime value={snapshot.generated_at} />
        </>
      ) : coverage ? (
        `${status === "stale" ? "Stale data" : "Catching up"} · Coverage not yet available`
      ) : (
        `${label} not yet available`
      )}
      {snapshot.catalog_lag_seconds != null && snapshot.catalog_lag_seconds >= 60 && (
        <> · Catalog changes pending for {Math.ceil(snapshot.catalog_lag_seconds / 60)} min</>
      )}
    </p>
  );
}
