"use client";

import { useFeedPreferences } from "./feed-preferences";

export function LoadingSkeleton({ label = "Loading articles…", kind = "feed" }: { label?: string; kind?: "feed" | "form" | "topics" }) {
  const { view } = useFeedPreferences();
  const compact = kind === "feed" && view === "compact";
  return <div role="status" aria-label={label}>
    <span className="sr-only">{label}</span>
    <div aria-hidden="true" className={`loading-skeleton ${kind}${compact ? " compact" : ""}`}>
      {Array.from({ length: kind === "form" ? 3 : 6 }, (_, index) => <div key={index} className="skeleton-item">
        {kind === "feed" && !compact && <div className="shimmer skeleton-image" />}
        <div className="shimmer skeleton-line" /><div className="shimmer skeleton-line short" />
      </div>)}
    </div>
  </div>;
}
