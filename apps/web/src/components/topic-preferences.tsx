"use client";
import { LoadingSkeleton } from "./loading-skeleton";
import { UserSettingsLayout } from "./user-settings-layout";
import { useEffect, useState } from "react";
import type { Topic } from "@/lib/types";
import { userRequest, type Preferences } from "@/lib/user";
import { useUser, AccountGate } from "./user-account";
import { CatalogIcon } from "./catalog-icon";

function TopicChoices({ topics }: { topics: Topic[] }) {
  const { user } = useUser();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    userRequest<Preferences>("preferences", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => setSelected(value.topic_ids))
      .catch(() => {
        if (!controller.signal.aborted)
          setMessage("Couldn’t load your topics. Reload to try again.");
      });
    return () => controller.abort();
  }, []);
  async function save() {
    setBusy(true);
    setMessage("");
    try {
      const result = await userRequest<Preferences>("preferences", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": user!.csrf_token,
        },
        body: JSON.stringify({ topic_ids: selected }),
      });
      setSelected(result.topic_ids);
      setMessage("Your topics are saved.");
    } catch {
      setMessage(
        "Couldn’t save your topics. Try again, or sign in if your session expired.",
      );
    } finally {
      setBusy(false);
    }
  }
  const visible = topics.filter((topic) =>
    topic.name.toLowerCase().includes(query.toLowerCase()),
  );
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
          (selected === null
            ? "Loading your topics…"
            : `${selected.length} topics selected`)}
      </p>
      {selected === null && <LoadingSkeleton kind="topics" label="Loading your topics…" />}
      {selected !== null && (
        <>
          <div className="topic-choice-grid">
            {visible.map((topic) => (
              <button
                key={topic.id}
                type="button"
                className="topic-choice"
                aria-pressed={selected.includes(topic.id)}
                disabled={busy || (!selected.includes(topic.id) && selected.length >= 100)}
                onClick={() => {
                  setSelected(selected.includes(topic.id)
                    ? selected.filter((id) => id !== topic.id)
                    : [...selected, topic.id]);
                  setMessage("");
                }}
              >
                <CatalogIcon url={topic.logo_url} />
                <span>{topic.name}</span>
              </button>
            ))}
          </div>
          {!visible.length && <p>No topics match your search.</p>}
          <div className="profile-form-actions">
            <button className="settings-button" disabled={busy} onClick={save}>
              {busy ? "Please wait…" : "Save topics"}
            </button>
            <button
              className="settings-button settings-button-ghost"
              disabled={busy || !selected.length}
              onClick={() => setSelected([])}
            >
              Clear selection
            </button>
          </div>
        </>
      )}
    </>
  );
}
export function TopicPreferences({ topics }: { topics: Topic[] }) {
  return (
    <AccountGate returnTo="/settings/topics">
      <UserSettingsLayout section="topics"><section className="profile-panel"><TopicChoices topics={topics} /></section></UserSettingsLayout>
    </AccountGate>
  );
}
