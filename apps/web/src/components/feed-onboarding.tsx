"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { InfiniteChoices } from "./infinite-choices";
import type { Topic } from "@/lib/types";
import { userRequest, type Preferences } from "@/lib/user";
import { useUser } from "./user-account";
import styles from "./feed-onboarding.module.css";

export function FeedOnboarding() {
  const { user } = useUser();
  const [eligible, setEligible] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const saving = useRef<AbortController | null>(null);
  const shown = eligible && !dismissed;

  useEffect(() => {
    if (!user || dismissed) return;
    const controller = new AbortController();
    const requestSignal = () => AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]);
    async function load() {
      const preferences = await userRequest<Preferences>("preferences", {
        signal: requestSignal(),
      });
      if (controller.signal.aborted) return;
      if (preferences.topic_ids.length) {
        setEligible(false);
        return;
      }
      setEligible(true);
    }
    void load().catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [revision, user, dismissed]);

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
  useEffect(() => () => saving.current?.abort(), []);

  function retry() {
    setFailed(false);
    setRevision((value) => value + 1);
  }

  async function save() {
    if (!user || saving.current || selected.length < 3) return;
    const controller = new AbortController();
    saving.current = controller;
    setBusy(true);
    setError("");
    try {
      await userRequest<Preferences>("preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify({ topic_ids: selected }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
      });
      if (!controller.signal.aborted) setDismissed(true);
    } catch {
      if (!controller.signal.aborted) setError("Couldn’t save your topics. Please try again.");
    } finally {
      saving.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  if (dismissed) return null;
  if (!eligible)
    return failed ? (
      <p role="alert">
        Couldn’t load your topics. <button onClick={retry}>Try again</button>
      </p>
    ) : null;

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="feed-topics-title"
      aria-describedby="feed-topics-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) setDismissed(true);
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
            onClick={() => setDismissed(true)}
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
