"use client";

import { LayoutGrid, List, Rows3 } from "lucide-react";
import { availableReaderLayouts } from "@/lib/reader-layouts";
import { useFeedPreferences } from "./feed-preferences";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";
import { useUser } from "./user-account";
import { useSettingsToast } from "./settings-toast";

/** One selection applies to every feed through the shared preference provider. */
export function ArticleViewSettings() {
  const { view, loading, busy, unavailable, error, setView, refresh } = useFeedPreferences();
  const { loading: checkingAccount } = useUser();
  const notify = useSettingsToast();
  if (loading)
    return (
      <LoadingReveal
        loading
        fallback={<LoadingSkeleton kind="form" label="Loading article layout…" />}
      />
    );
  if (unavailable)
    return (
      <section className="profile-load-error" aria-label="Article feed layout" role="status">
        <h2>Couldn’t load article layout</h2>
        <button className="settings-button" onClick={refresh}>
          Retry
        </button>
      </section>
    );
  return (
    <section
      className="profile-panel profile-form"
      aria-label="Article feed layout"
      aria-busy={busy || checkingAccount}
    >
      <fieldset className="feed-content-preferences">
        <legend>Article feed layout</legend>
        <p className="profile-description">Use the same article layout across all your feeds.</p>
        <div className="feed-layout-options">
          {availableReaderLayouts.map(({ id, value, label, description }) => {
            const Icon = id === "grid" ? LayoutGrid : id === "stacked" ? Rows3 : List;
            return (
              <label className={`feed-layout-option${view === value ? " selected" : ""}`} key={id}>
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
                    checked={view === value}
                    disabled={checkingAccount || (busy && view !== value)}
                    onChange={() => {
                      void setView(value).then((saved) => {
                        if (saved) notify("Article layout saved.");
                      });
                    }}
                    aria-label={label}
                  />
                </div>
                <p>{description}</p>
              </label>
            );
          })}
        </div>
      </fieldset>
      {error && (
        <p className="profile-feedback error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
