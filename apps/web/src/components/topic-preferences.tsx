"use client";
import { LoadingReveal } from "./loading-reveal";
import { SaveFeedback } from "./motion-icon";
import { InfiniteChoices } from "./infinite-choices";
import { LoadingSkeleton } from "./loading-skeleton";
import { UserSettingsLayout } from "./user-settings-layout";
import { useState } from "react";
import type { Topic } from "@/lib/types";
import { useTopicFollows } from "./topic-follows";
import { AccountGate, useUser } from "./user-account";
import { CatalogIcon } from "./catalog-icon";

function TopicChoices({ topics }: { topics?: Topic[] }) {
  const follows = useTopicFollows();
  const [draft, setSelected] = useState<string[] | null>(null);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const busy = saving || follows.busy.length > 0;
  const selected = follows.loading || follows.unavailable ? null : (draft ?? follows.ids);
  async function save() {
    if (!selected || busy) return;
    setSaving(true);
    setMessage("");
    const result = await follows.save(selected);
    if (result) {
      setSelected(null);
      setMessage("Your topics are saved.");
    } else setMessage("Couldn’t save your topics. Try again, or sign in if your session expired.");
    setSaving(false);
  }
  return (
    <>
      <p className="profile-description">Choose up to 100 topics for your feed.</p>
      <label className="topic-search">
        Find a topic
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search topics"
        />
      </label>
      <p role="status">
        {message ||
          (selected === null ? "Loading your topics…" : `${selected.length} topics selected`)}
      </p>
      {follows.unavailable && (
        <p role="alert">
          Couldn’t load your topics. <button onClick={follows.refresh}>Try again</button>
        </p>
      )}
      <LoadingReveal
        loading={follows.loading}
        fallback={<LoadingSkeleton kind="topics" label="Loading your topics…" />}
      >
        {selected !== null && (
          <>
            <InfiniteChoices items={topics} label="topics" query={query}>
              {(choices) => (
                <div className="topic-choice-grid">
                  {choices.map((topic) => (
                    <button
                      key={topic.id}
                      type="button"
                      className="topic-choice"
                      aria-pressed={selected.includes(topic.id)}
                      disabled={busy || (!selected.includes(topic.id) && selected.length >= 100)}
                      onClick={() => {
                        setSelected(
                          selected.includes(topic.id)
                            ? selected.filter((id) => id !== topic.id)
                            : [...selected, topic.id],
                        );
                        setMessage("");
                      }}
                    >
                      <CatalogIcon url={topic.logo_url} variants={topic.logo_variants} />
                      <span>{topic.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </InfiniteChoices>
            <div className="profile-form-actions">
              <button className="settings-button" disabled={busy} onClick={save}>
                <SaveFeedback busy={busy} saved={message === "Your topics are saved."} />
                {busy ? "Please wait…" : "Save topics"}
              </button>
              <button
                className="settings-button settings-button-ghost"
                disabled={busy || !selected.length}
                onClick={() => {
                  setSelected([]);
                  setMessage("");
                }}
              >
                Clear selection
              </button>
            </div>
          </>
        )}
      </LoadingReveal>
    </>
  );
}
export function TopicPreferences({ topics }: { topics?: Topic[] }) {
  const { user } = useUser();
  return (
    <UserSettingsLayout section="topics">
      <section className="profile-panel">
        <AccountGate
          returnTo="/settings/topics"
          loadingFallback={<LoadingSkeleton kind="topics" label="Loading your topics…" />}
        >
          <TopicChoices key={user?.user_id} topics={topics} />
        </AccountGate>
      </section>
    </UserSettingsLayout>
  );
}
