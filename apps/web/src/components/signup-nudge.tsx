"use client";

import { useEffect, useState, useSyncExternalStore, type MouseEvent } from "react";
import { readerLocation, readerLoginLink } from "@/lib/reader-runtime";
import { isArticleSlug } from "@/lib/reader-routes";
import { useReaderContext } from "./reader-context";
import { useUser } from "./user-account";
import {
  invitationDismissed,
  rememberInvitationDismissal,
  useReaderPrompt,
  useReaderPromptCoordinator,
} from "./reader-prompts";
import styles from "./signup-nudge.module.css";

const seenKey = "devfeed:signup-nudge-articles";
const threshold = 3;
const changedEvent = "devfeed:signup-nudge-changed";

function subscribe(listener: () => void) {
  window.addEventListener(changedEvent, listener);
  return () => window.removeEventListener(changedEvent, listener);
}

function currentSignupLink() {
  const location = readerLocation();
  return readerLoginLink(`${location.pathname}${location.search}`);
}

function refreshSignupLink(event: MouseEvent<HTMLAnchorElement>) {
  event.currentTarget.href = currentSignupLink().href;
}

function createVisitStore() {
  let seen = new Set<string>();
  let dismissed = false;
  const count = () => {
    try {
      const value = JSON.parse(sessionStorage.getItem(seenKey) ?? "[]");
      if (Array.isArray(value))
        for (const item of value.slice(-20))
          if (typeof item === "string" && isArticleSlug(item)) seen.add(item);
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

export function SignupNudge() {
  const { user, loading, unavailable } = useUser();
  const { currentArticle, promptEligibility } = useReaderContext();
  const articleSlug = currentArticle?.slug;
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
    promptEligibility.signup;
  const shown = useReaderPrompt("signup", eligible);

  useEffect(() => {
    if (loading || unavailable || user) return;
    if (articleSlug) visit.visit(articleSlug);
  }, [articleSlug, loading, unavailable, user, visit]);

  if (!shown) return null;

  const link = currentSignupLink();

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
        <a
          className={`button primary ${styles.signup}`}
          href={link.href}
          target={link.target}
          rel={link.rel}
          onClick={refreshSignupLink}
          onAuxClick={refreshSignupLink}
          onContextMenu={refreshSignupLink}
        >
          Create account
        </a>
      </div>
    </aside>
  );
}
