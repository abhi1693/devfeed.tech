"use client";

import { Columns3, LayoutGrid, List, Rows3 } from "lucide-react";
import { useState } from "react";
import { availableReaderLayouts } from "@/lib/reader-layouts";
import { useFeedPreferences } from "./feed-preferences";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";
import { useUser } from "./user-account";
import { useSettingsToast } from "./settings-toast";
import { readerLoginLink } from "@/lib/reader-runtime";
import type { FeedViewScope } from "@/lib/feed-view-scope";
import { ScopedFeedPreferences } from "./scoped-feed-preferences";
import styles from "./article-view-settings.module.css";

/** Columns is a personal workspace; the other layouts apply to article feeds. */
export function ArticleViewSettings() {
  const { user } = useUser();
  return (
    <FeedLayoutSettings key={user?.user_id ?? "guest"} initialScope={user ? "my_feed" : "latest"} />
  );
}

function FeedLayoutSettings({ initialScope }: { initialScope: FeedViewScope }) {
  const [scope, setScope] = useState(initialScope);
  return (
    <ScopedFeedPreferences scope={scope}>
      <ArticleViewOptions scope={scope} onScopeChange={setScope} />
    </ScopedFeedPreferences>
  );
}

export function ArticleViewOptions({
  scope = "my_feed",
  onScopeChange,
}: {
  scope?: FeedViewScope;
  onScopeChange?: (scope: FeedViewScope) => void;
}) {
  const { view, loading, busy, unavailable, error, setView, refresh } = useFeedPreferences();
  const { user, loading: checkingAccount } = useUser();
  const selectedView = !user && view === "columns" ? "cards" : view;
  const notify = useSettingsToast();
  return (
    <section
      className="profile-panel profile-form"
      aria-label="Article feed layout"
      aria-busy={loading || busy || checkingAccount}
    >
      {onScopeChange && (
        <div className={styles.scope}>
          <label htmlFor="article-layout-feed">Feed</label>
          <select
            id="article-layout-feed"
            value={scope}
            disabled={busy || checkingAccount}
            onChange={(event) => onScopeChange(event.target.value as FeedViewScope)}
          >
            <option value="my_feed">My feed</option>
            <option value="latest">Latest</option>
            <option value="trending">Trending</option>
            <option value="read_later">Read later</option>
            <optgroup label="Article feeds">
              <option value="content:article">Articles</option>
              <option value="content:news">News</option>
              <option value="content:tutorial">Tutorials</option>
              <option value="content:release">Releases</option>
              <option value="content:comparison">Comparisons</option>
              <option value="content:opinion">Opinions</option>
            </optgroup>
          </select>
        </div>
      )}
      {loading ? (
        <LoadingReveal
          loading
          fallback={<LoadingSkeleton kind="form" label="Loading article layout…" />}
        />
      ) : unavailable ? (
        <div className="profile-load-error" role="status">
          <h2>Couldn’t load article layout</h2>
          <button className="settings-button" onClick={refresh}>
            Retry
          </button>
        </div>
      ) : (
        <fieldset className="feed-content-preferences">
          <legend>Article feed layout</legend>
          <p className="profile-description">
            Choose a layout for this feed. Other feeds keep their own layout.
          </p>
          <div className="feed-layout-options">
            {availableReaderLayouts.map(({ id, value, label, description }) => {
              const Icon =
                id === "grid"
                  ? LayoutGrid
                  : id === "stacked"
                    ? Rows3
                    : id === "columns"
                      ? Columns3
                      : List;
              return (
                <label
                  className={`feed-layout-option${selectedView === value ? " selected" : ""}`}
                  key={id}
                >
                  <div className={`feed-layout-preview ${value}`} aria-hidden="true">
                    {[0, 1, 2].map((index) => (
                      <div className="feed-layout-preview-item" key={index}>
                        <span className="preview-image" />
                        <span className="preview-lines">
                          <i />
                          <i />
                        </span>
                        <span className="preview-metric" />
                      </div>
                    ))}
                  </div>
                  <div className="feed-layout-option-label">
                    <Icon size={17} aria-hidden="true" />
                    <span>{label}</span>
                    <input
                      type="radio"
                      name="article-view"
                      value={value}
                      checked={selectedView === value}
                      disabled={
                        (id === "columns" && (!user || scope !== "my_feed")) ||
                        checkingAccount ||
                        (busy && view !== value)
                      }
                      onChange={() => {
                        void setView(value).then((saved) => {
                          if (saved) notify("Article layout saved.");
                        });
                      }}
                      aria-label={label}
                    />
                  </div>
                  <p>
                    {id === "columns" && !user
                      ? "Sign in to arrange My feed into columns."
                      : description}
                  </p>
                </label>
              );
            })}
          </div>
          {!user && <a {...readerLoginLink("/settings/appearance")}>Sign in to use Columns</a>}
        </fieldset>
      )}
      {error && (
        <p className="profile-feedback error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
