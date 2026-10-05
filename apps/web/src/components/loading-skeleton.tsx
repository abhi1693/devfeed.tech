"use client";

import { useFeedPreferences } from "./feed-preferences";
import { ArticleTableFrame } from "./article-table";

export function LoadingSkeleton({
  label = "Loading articles…",
  kind = "feed",
}: {
  label?: string;
  kind?: "feed" | "form" | "topics" | "sources";
}) {
  const { view, loading } = useFeedPreferences();
  const compact = kind === "feed" && view === "compact";
  return (
    <div role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      {kind === "feed" && loading ? null : (
        <div
          aria-hidden="true"
          className={`loading-skeleton ${kind}${kind === "topics" ? " topic-choice-grid" : ""}${kind === "sources" ? " source-choice-grid" : ""}${compact ? " compact" : ""}`}
        >
          {compact ? (
            <ArticleTableFrame showHeader>
              {Array.from({ length: 6 }, (_, index) => (
                <tr key={index} className="skeleton-list-row">
                  <td>
                    <div className="shimmer skeleton-line" />
                  </td>
                  <td className="source-column">
                    <div className="shimmer skeleton-line short" />
                  </td>
                  <td className="date-column">
                    <div className="shimmer skeleton-line" />
                  </td>
                  <td className="bookmark-column">
                    <span className="article-bookmark">
                      <span className="shimmer skeleton-bookmark" />
                    </span>
                  </td>
                </tr>
              ))}
            </ArticleTableFrame>
          ) : (
            Array.from({ length: kind === "form" ? 3 : 6 }, (_, index) => (
              <div key={index} className="skeleton-item">
                {kind === "feed" && <div className="shimmer skeleton-image" />}
                {kind === "sources" || kind === "topics" ? (
                  <>
                    <div className="shimmer skeleton-topic-icon" />
                    <div className="shimmer skeleton-line short" />
                  </>
                ) : (
                  <>
                    <div className="shimmer skeleton-line" />
                    <div className="shimmer skeleton-line short" />
                  </>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
