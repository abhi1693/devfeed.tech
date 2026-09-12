"use client";
import { LoadingSkeleton } from "./loading-skeleton";

import { useRouter } from "next/navigation";
import { contentTypes } from "@/lib/feed-query";
import { useState } from "react";
import { LayoutGrid, List } from "lucide-react";
import { AccountGate } from "./user-account";
import { UserSettingsLayout } from "./user-settings-layout";
import { useFeedPreferences, type FeedDisplay } from "./feed-preferences";

const contentOptions = {
  article: ["Articles", "Deep dives, explainers, and developer stories."],
  news: ["News", "Updates from across the developer community."],
  tutorial: ["Tutorials", "Step-by-step guides and practical walkthroughs."],
  release: ["Releases", "New versions, features, and changelogs."],
  comparison: ["Comparisons", "Side-by-side looks at tools and technologies."],
  opinion: ["Opinions", "Perspectives and commentary on software development."],
} as const;

export function FeedSettings() {
  return (
    <AccountGate returnTo="/settings/feed">
      <UserSettingsLayout section="feed">
        <FeedSettingsForm />
      </UserSettingsLayout>
    </AccountGate>
  );
}

function FeedSettingsForm() {
  const router = useRouter();
  const { view, content_types, loading, busy, unavailable, error, save, refresh } =
    useFeedPreferences();
  const [selected, setSelected] = useState(view);
  const [baseline, setBaseline] = useState(view);
  const typeKey = content_types.join(",");
  const [types, setTypes] = useState(content_types);
  const [typeBaseline, setTypeBaseline] = useState(typeKey);
  if (typeBaseline !== typeKey) {
    if (types.join(",") === typeBaseline) setTypes(content_types);
    setTypeBaseline(typeKey);
  }
  const [message, setMessage] = useState("");
  if (baseline !== view) {
    if (selected === baseline) setSelected(view);
    setBaseline(view);
  }
  if (unavailable)
    return (
      <section className="profile-load-error" role="status">
        <h2>Couldn’t load feed settings</h2>
        <button className="settings-button" onClick={refresh}>
          Retry
        </button>
      </section>
    );
  if (loading) return <LoadingSkeleton kind="form" label="Loading feed settings…" />;
  const dirty = selected !== view || types.join(",") !== typeKey;
  function choose(value: FeedDisplay["view"]) {
    setSelected(value);
    setMessage("");
  }
  return (
    <section className="profile-panel" aria-label="Feed preferences">
      <p className="profile-description">Choose how articles appear in your feeds.</p>
      <form
        className="profile-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!dirty || busy || !types.length) return;
          setMessage("");
          if (await save({ view: selected, content_types: types })) {
            setMessage("Feed settings saved.");
            router.refresh();
          }
        }}
      >
        <fieldset disabled={busy}>
          <legend className="sr-only">Feed layout</legend>
          <div className="feed-layout-options">
            {(
              [
                ["cards", "Cards", "Image previews with room to browse.", LayoutGrid],
                [
                  "compact",
                  "Compact list",
                  "More headlines, sources, and activity at a glance.",
                  List,
                ],
              ] as const
            ).map(([value, title, description, Icon]) => (
              <label
                className={`feed-layout-option${selected === value ? " selected" : ""}`}
                key={value}
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
                  <span>{title}</span>
                  <input
                    type="radio"
                    name="feed-view"
                    value={value}
                    checked={selected === value}
                    onChange={() => choose(value)}
                    aria-label={title}
                  />
                </div>
                <p>{description}</p>
              </label>
            ))}
          </div>
        </fieldset>
        <section className="feed-content-section">
          <fieldset className="feed-content-preferences" disabled={busy}>
            <legend>Content types</legend>
            <p className="profile-description" id="content-types-help">
              Choose the kinds of posts you want in your feeds.
            </p>
            <div className="feed-content-options" aria-describedby="content-types-help">
              {contentTypes.map((kind) => (
                <label className="feed-content-option" key={kind}>
                  <input
                    type="checkbox"
                    name="content_types"
                    value={kind}
                    aria-label={contentOptions[kind][0]}
                    aria-describedby={`content-type-${kind}-description`}
                    checked={types.includes(kind)}
                    onChange={() => {
                      setTypes(
                        contentTypes.filter((type) =>
                          type === kind ? !types.includes(kind) : types.includes(type),
                        ),
                      );
                      setMessage("");
                    }}
                  />
                  <span>
                    <span className="feed-content-label">{contentOptions[kind][0]}</span>
                    <span
                      className="feed-content-description"
                      id={`content-type-${kind}-description`}
                    >
                      {contentOptions[kind][1]}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            <p className="feed-content-help">You can still browse any type from the feed tabs.</p>
            {!types.length && (
              <p className="profile-feedback error" role="alert">
                Select at least one content type.
              </p>
            )}
          </fieldset>
        </section>
        {error ? (
          <p className="profile-feedback error" role="alert">
            {error}
          </p>
        ) : message ? (
          <p className="profile-feedback" role="status">
            {message}
          </p>
        ) : null}
        <div className="profile-form-actions">
          <button
            className="settings-button settings-button-ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              choose("cards");
              setTypes([...contentTypes]);
            }}
          >
            Reset to defaults
          </button>
          <button
            className="settings-button"
            type="submit"
            disabled={busy || !dirty || !types.length}
          >
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
        {dirty && (
          <p className="profile-unsaved" role="status">
            You have unsaved changes.
          </p>
        )}
      </form>
    </section>
  );
}
