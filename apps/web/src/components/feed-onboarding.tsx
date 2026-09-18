"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { readerRequest } from "@/lib/reader-runtime";
import type { Topic } from "@/lib/types";
import { userRequest, type Preferences } from "@/lib/user";
import { useUser } from "./user-account";
import styles from "./feed-onboarding.module.css";

export function FeedOnboarding() {
  const { user } = useUser();
  const [eligible, setEligible] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [topics, setTopics] = useState<Topic[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const [loadingMore, setLoadingMore] = useState(true);
  const nextCursor = useRef<string | null>("0");
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
      setLoadingMore(true);
      const visited = new Set<string>();
      let cursor = nextCursor.current;
      while (cursor !== null && !visited.has(cursor)) {
        visited.add(cursor);
        const response = await readerRequest(
          `/api/v1/topics?sort=articles&offset=${encodeURIComponent(cursor)}`,
          { signal: requestSignal() },
        );
        if (!response.ok) throw new Error("Topics unavailable");
        const page = (await response.json()) as { items: Topic[]; next_cursor: string | null };
        if (controller.signal.aborted) return;
        setTopics((previous) =>
          Array.from(
            new Map(
              [...(previous ?? []), ...page.items].map((topic) => [topic.id, topic]),
            ).values(),
          ),
        );
        cursor = page.next_cursor;
        nextCursor.current = cursor;
      }
    }
    void load()
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingMore(false);
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
    setLoadingMore(true);
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

  const search = query.trim().toLowerCase();
  const visible = (topics ?? []).filter((topic) => topic.name.toLowerCase().includes(search));
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
          {failed && (
            <p role="alert">
              {topics?.length ? "Couldn’t load more topics." : "Couldn’t load topics."}{" "}
              <button type="button" onClick={retry}>
                Try again
              </button>
            </p>
          )}
          {topics === null ? (
            !failed && <p role="status">Loading topics…</p>
          ) : (
            <>
              <p className={styles.hint}>Most articles first</p>
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
              {!visible.length && (
                <p>
                  {loadingMore
                    ? "Still looking for matching topics…"
                    : "No topics match your search."}
                </p>
              )}
              {loadingMore && <p role="status">Loading more topics…</p>}
              {!loadingMore && !failed && topics.length < 3 && (
                <p>There aren’t enough topics available yet. Try again later.</p>
              )}
            </>
          )}
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
