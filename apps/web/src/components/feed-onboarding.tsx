"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { InfiniteChoices } from "./infinite-choices";
import type { Topic } from "@/lib/types";
import { useTopicFollows } from "./topic-follows";
import { useUser } from "./user-account";
import styles from "./feed-onboarding.module.css";
import { useReaderPrompt, useReaderPromptCoordinator } from "./reader-prompts";

export function FeedOnboarding() {
  const { user } = useUser();
  return <TopicOnboarding key={user?.user_id ?? "guest"} />;
}

function TopicOnboarding() {
  const { user } = useUser();
  const follows = useTopicFollows();
  const prompts = useReaderPromptCoordinator();
  const eligible = !!user && !follows.loading && !follows.unavailable && !follows.ids.length;
  const [dismissed, setDismissed] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const saving = useRef(false);
  const reserved = useReaderPrompt(
    "onboarding",
    !!user && !dismissed && (follows.loading || eligible),
  );
  const shown = eligible && !dismissed && reserved;

  useEffect(() => {
    if (!shown) return;
    const element = dialog.current!;
    const overflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
    };
  }, [shown]);
  async function save() {
    if (!user || saving.current || selected.length < 3) return;
    saving.current = true;
    setBusy(true);
    setError("");
    const result = await follows.save(selected);
    if (result) {
      prompts?.pause();
      setDismissed(true);
    } else setError("Couldn’t save your topics. Please try again.");
    saving.current = false;
    setBusy(false);
  }

  if (dismissed) return null;
  if (!eligible)
    return follows.unavailable ? (
      <p role="alert">
        Couldn’t load your topics. <button onClick={follows.refresh}>Try again</button>
      </p>
    ) : null;
  if (!shown) return null;

  function dismiss() {
    prompts?.pause();
    setDismissed(true);
  }

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="feed-topics-title"
      aria-describedby="feed-topics-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) dismiss();
      }}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <header className={styles.header}>
          <h2 id="feed-topics-title">Choose your topics</h2>
          <button
            type="button"
            className={styles.close}
            aria-label="Close"
            disabled={busy}
            onClick={dismiss}
          >
            <X size={18} />
          </button>
          <p id="feed-topics-description">Select at least 3 topics for your feed.</p>
        </header>
        <div className={styles.search}>
          <input
            type="search"
            aria-label="Search topics"
            placeholder="Search topics"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className={styles.topics}>
          <p className={styles.hint}>Most articles first</p>
          <InfiniteChoices<Topic> label="topics" sort="articles" query={query}>
            {(visible, complete) => (
              <>
                <div className={styles.choices}>
                  {visible.map((topic) => (
                    <label key={topic.id} className={styles.choice}>
                      <input
                        type="checkbox"
                        checked={selected.includes(topic.id)}
                        disabled={busy || (!selected.includes(topic.id) && selected.length >= 100)}
                        onChange={(event) => {
                          setSelected((previous) =>
                            event.target.checked
                              ? [...previous, topic.id]
                              : previous.filter((id) => id !== topic.id),
                          );
                          setError("");
                        }}
                      />
                      <span>{topic.name}</span>
                    </label>
                  ))}
                </div>
                {complete && !query && visible.length < 3 && (
                  <p>There aren’t enough topics available yet. Try again later.</p>
                )}
              </>
            )}
          </InfiniteChoices>
        </div>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <footer className={styles.footer}>
          <span role="status">{selected.length} selected</span>
          <button type="submit" className="button primary" disabled={busy || selected.length < 3}>
            {busy ? "Saving…" : "Save"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
