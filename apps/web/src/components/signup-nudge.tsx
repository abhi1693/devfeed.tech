"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { readerLoginLink } from "@/lib/reader-runtime";
import { useUser } from "./user-account";
import {
  invitationDismissed,
  rememberInvitationDismissal,
  useReaderPrompt,
  useReaderPromptCoordinator,
} from "./reader-prompts";
import styles from "./signup-nudge.module.css";

const seenKey = "devfeed:signup-nudge-articles";
const articlePath = /^\/articles\/([a-z0-9][a-z0-9-]{0,199})$/i;
const authPath = /^\/(?:login|register|extension\/login-complete)(?:\/|$)/;
const threshold = 3;
const changedEvent = "devfeed:signup-nudge-changed";

function subscribe(listener: () => void) {
  window.addEventListener(changedEvent, listener);
  return () => window.removeEventListener(changedEvent, listener);
}

function createVisitStore() {
  let seen = new Set<string>();
  let dismissed = false;
  const count = () => {
    try {
      const value = JSON.parse(sessionStorage.getItem(seenKey) ?? "[]");
      if (Array.isArray(value))
        for (const item of value.slice(-20))
          if (typeof item === "string" && articlePath.test(`/articles/${item}`)) seen.add(item);
    } catch {
      // Keep navigation and dismissal usable when browser storage is blocked.
    }
    seen = new Set([...seen].slice(-20));
    return seen.size;
  };
  return {
    count,
    dismissed: () => dismissed || invitationDismissed(),
    visit: (slug: string) => {
      count();
      if (seen.has(slug)) return;
      seen.add(slug);
      seen = new Set([...seen].slice(-20));
      try {
        sessionStorage.setItem(seenKey, JSON.stringify([...seen]));
      } catch {
        // This store remains mounted across reader navigation.
      }
      window.dispatchEvent(new Event(changedEvent));
    },
    dismiss: () => {
      dismissed = true;
      rememberInvitationDismissal();
      window.dispatchEvent(new Event(changedEvent));
    },
  };
}

export function SignupNudge({ pathname }: { pathname: string }) {
  const { user, loading, unavailable } = useUser();
  const [visit] = useState(createVisitStore);
  const seenCount = useSyncExternalStore(subscribe, visit.count, () => 0);
  const dismissed = useSyncExternalStore(subscribe, visit.dismissed, () => false);
  const prompts = useReaderPromptCoordinator();
  const eligible =
    seenCount >= threshold &&
    !loading &&
    !unavailable &&
    !user &&
    !dismissed &&
    !prompts?.guestDismissed &&
    !prompts?.modalOpen &&
    !authPath.test(pathname);
  const shown = useReaderPrompt("signup", eligible);

  useEffect(() => {
    if (loading || unavailable || user) return;
    const slug = articlePath.exec(pathname)?.[1];
    if (!slug) return;
    visit.visit(slug);
  }, [loading, unavailable, pathname, user, visit]);

  if (!shown) return null;

  const returnTo = `${pathname}${typeof window !== "undefined" ? window.location.search : ""}`;
  const link = readerLoginLink(returnTo);

  return (
    <aside className={styles.nudge} aria-label="Create a DevFeed account">
      <div className={styles.copy}>
        <strong>Create your DevFeed account.</strong>
        <p>Save articles, follow topics, and get recommendations shaped by your interests.</p>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={`button ${styles.dismiss}`}
          onClick={() => {
            visit.dismiss();
            prompts?.dismissGuestInvitations();
          }}
        >
          Not now
        </button>
        <a className={`button primary ${styles.signup}`} {...link}>
          Create account
        </a>
      </div>
    </aside>
  );
}
