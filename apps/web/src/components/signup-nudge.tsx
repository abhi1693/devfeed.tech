"use client";

import { useEffect, useSyncExternalStore } from "react";
import { readerLoginLink } from "@/lib/reader-runtime";
import { useUser } from "./user-account";
import styles from "./signup-nudge.module.css";

const seenKey = "devfeed:signup-nudge-articles";
const articlePath = /^\/articles\/([a-z0-9][a-z0-9-]{0,199})$/i;
const threshold = 3;
const changedEvent = "devfeed:signup-nudge-changed";

function subscribe(listener: () => void) {
  window.addEventListener(changedEvent, listener);
  return () => window.removeEventListener(changedEvent, listener);
}

function readSeenArticles() {
  try {
    const value = JSON.parse(sessionStorage.getItem(seenKey) ?? "[]");
    return new Set(
      Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [],
    );
  } catch {
    return new Set<string>();
  }
}

function saveSeenArticles(seen: Set<string>) {
  try {
    sessionStorage.setItem(seenKey, JSON.stringify([...seen].slice(-20)));
  } catch {
    // The in-memory state still handles this visit when storage is unavailable.
  }
  window.dispatchEvent(new Event(changedEvent));
}

function seenArticleCount() {
  return readSeenArticles().size;
}

export function SignupNudge({ pathname }: { pathname: string }) {
  const { user, loading, unavailable } = useUser();
  const seenCount = useSyncExternalStore(subscribe, seenArticleCount, () => 0);

  useEffect(() => {
    if (loading || unavailable || user) return;
    const slug = articlePath.exec(pathname)?.[1];
    if (!slug) return;
    const seen = readSeenArticles();
    if (seen.has(slug)) return;
    seen.add(slug);
    saveSeenArticles(seen);
  }, [loading, unavailable, pathname, user]);

  if (seenCount < threshold || user) return null;

  const returnTo = `${pathname}${typeof window !== "undefined" ? window.location.search : ""}`;
  const link = readerLoginLink(returnTo);

  return (
    <aside className={styles.nudge} aria-label="Create a DevFeed account">
      <div className={styles.copy}>
        <strong>Create your DevFeed account.</strong>
        <p>Save articles, follow topics, and get recommendations shaped by your interests.</p>
      </div>
      <div className={styles.actions}>
        <a className={`button primary ${styles.signup}`} {...link}>
          Create account
        </a>
      </div>
    </aside>
  );
}
