"use client";
import { LoadingSkeleton } from "./loading-skeleton";

import { useState } from "react";
import { LayoutGrid, List } from "lucide-react";
import { AccountGate } from "./user-account";
import { UserSettingsLayout } from "./user-settings-layout";
import { useFeedPreferences, type FeedDisplay } from "./feed-preferences";

export function FeedSettings() {
  return <AccountGate returnTo="/settings/feed"><UserSettingsLayout section="feed"><FeedSettingsForm /></UserSettingsLayout></AccountGate>;
}

function FeedSettingsForm() {
  const { view, loading, busy, unavailable, error, save, refresh } = useFeedPreferences();
  const [selected, setSelected] = useState(view);
  const [baseline, setBaseline] = useState(view);
  const [message, setMessage] = useState("");
  if (baseline !== view) {
    if (selected === baseline) setSelected(view);
    setBaseline(view);
  }
  if (unavailable) return <section className="profile-load-error" role="status"><h2>Couldn’t load feed settings</h2><button className="settings-button" onClick={refresh}>Retry</button></section>;
  if (loading) return <LoadingSkeleton kind="form" label="Loading feed settings…" />;
  const dirty = selected !== view;
  function choose(value: FeedDisplay["view"]) { setSelected(value); setMessage(""); }
  return <section className="profile-panel" aria-label="Feed preferences">
    <p className="profile-description">Choose how articles appear in your feeds.</p>
    <form className="profile-form" onSubmit={async event => {
      event.preventDefault();
      if (!dirty || busy) return;
      setMessage("");
      if (await save(selected)) setMessage("Feed settings saved.");
    }}>
      <fieldset disabled={busy}>
        <legend className="sr-only">Feed layout</legend>
        <div className="feed-layout-options">
          {([ ["cards", "Cards", "Image previews with room to browse.", LayoutGrid], ["compact", "Compact list", "More headlines, sources, and activity at a glance.", List] ] as const).map(([value, title, description, Icon]) => (
            <label className={`feed-layout-option${selected === value ? " selected" : ""}`} key={value}>
              <div className={`feed-layout-preview ${value}`} aria-hidden="true">
                {[0, 1, 2].map(index => <div className="feed-layout-preview-item" key={index}><span className="preview-image" /><span className="preview-lines"><i /><i /></span><span className="preview-metric" /></div>)}
              </div>
              <div className="feed-layout-option-label"><Icon size={17} aria-hidden="true" /><span>{title}</span><input type="radio" name="feed-view" value={value} checked={selected === value} onChange={() => choose(value)} aria-label={title} /></div>
              <p>{description}</p>
            </label>
          ))}
        </div>
      </fieldset>
      {error ? <p className="profile-feedback error" role="alert">{error}</p> : message ? <p className="profile-feedback" role="status">{message}</p> : null}
      <div className="profile-form-actions">
        <button className="settings-button settings-button-ghost" type="button" disabled={busy} onClick={() => choose("cards")}>Reset to defaults</button>
        <button className="settings-button" type="submit" disabled={busy || !dirty}>{busy ? "Saving…" : "Save changes"}</button>
      </div>
      {dirty && <p className="profile-unsaved" role="status">You have unsaved changes.</p>}
    </form>
  </section>;
}
