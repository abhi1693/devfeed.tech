"use client";

import { LayoutGrid, List, RotateCcw } from "lucide-react";
import { useFeedPreferences } from "./feed-preferences";

export function FeedViewToggle() {
  const { view, setView, loading, busy, unavailable, error, refresh } = useFeedPreferences();
  if (unavailable)
    return (
      <div className="feed-view-toggle">
        <button
          type="button"
          onClick={refresh}
          aria-label="Retry feed settings"
          title="Retry feed settings"
        >
          <RotateCcw size={18} aria-hidden="true" />
        </button>
      </div>
    );
  return (
    <div className="feed-view-toggle" role="group" aria-label="Article view" aria-busy={busy}>
      {(
        [
          ["cards", "Grid view", LayoutGrid],
          ["compact", "List view", List],
        ] as const
      ).map(([value, label, Icon]) => (
        <button
          key={value}
          type="button"
          aria-label={label}
          title={label}
          aria-pressed={view === value}
          disabled={loading || busy || unavailable}
          onClick={() => void setView(value)}
        >
          <Icon size={18} aria-hidden="true" />
        </button>
      ))}
      {error && (
        <span className="feed-view-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
