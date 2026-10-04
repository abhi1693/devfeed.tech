"use client";

import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";

// Preserve existing anonymous Dev Card dismissals as a refusal of account invitations.
export const anonymousInvitationDismissedKey = "devfeed:dev-card-promo-dismissed";
export const promptPauseMs = 30_000;
type Prompt = "onboarding" | "dev-card" | "signup";
type Lease = { owner: string; kind: Prompt };
type Coordinator = {
  ready: boolean;
  guestDismissed: boolean;
  activeOwner: string | null;
  modalOpen: boolean;
  paused: boolean;
  acquire: (kind: Prompt, owner: string, requested?: boolean) => boolean;
  release: (owner: string) => void;
  dismissGuestInvitations: () => void;
  pause: () => void;
};

const Context = createContext<Coordinator | null>(null);
export const useReaderPromptCoordinator = () => useContext(Context);

export function invitationDismissed() {
  try {
    return sessionStorage.getItem(anonymousInvitationDismissedKey) === "true";
  } catch {
    return false;
  }
}

export function rememberInvitationDismissal() {
  try {
    sessionStorage.setItem(anonymousInvitationDismissedKey, "true");
  } catch {
    // The mounted reader also retains dismissal in memory.
  }
}

export function ReaderPromptsProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [guestDismissed, setGuestDismissed] = useState(false);
  const [active, setActive] = useState<Lease | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [paused, setPaused] = useState(false);
  const lease = useRef<Lease | null>(null);
  const quiet = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setGuestDismissed(invitationDismissed());
      setModalOpen(Boolean(document.querySelector("dialog[open]")));
      setReady(true);
    });
    // One observer coordinates native article/account dialogs with invitations.
    const observer = new MutationObserver(() => {
      setModalOpen(Boolean(document.querySelector("dialog[open]")));
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["open"],
      childList: true,
      subtree: true,
    });
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      clearTimeout(timer.current);
    };
  }, []);

  const acquire = useCallback((kind: Prompt, owner: string, requested = false) => {
    if (lease.current?.owner === owner) return true;
    if (kind !== "onboarding" && !requested && quiet.current) return false;
    if (document.querySelector("dialog[open]")) return false;
    // Explicit previews and onboarding can replace a banner, never another modal.
    if (lease.current && !(lease.current.kind === "signup" && (requested || kind === "onboarding")))
      return false;
    const next = { owner, kind };
    lease.current = next;
    setActive(next);
    return true;
  }, []);
  const release = useCallback((owner: string) => {
    if (lease.current?.owner !== owner) return;
    lease.current = null;
    setActive(null);
  }, []);
  const dismissGuestInvitations = useCallback(() => {
    rememberInvitationDismissal();
    setGuestDismissed(true);
  }, []);
  const pause = useCallback(() => {
    quiet.current = true;
    setPaused(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      quiet.current = false;
      setPaused(false);
    }, promptPauseMs);
  }, []);

  return (
    <Context.Provider
      value={{
        ready,
        guestDismissed,
        activeOwner: active?.owner ?? null,
        modalOpen,
        paused,
        acquire,
        release,
        dismissGuestInvitations,
        pause,
      }}
    >
      {children}
    </Context.Provider>
  );
}

// Reserve onboarding while its preferences load. Invitations cannot jump ahead of it.
export function useReaderPrompt(kind: "onboarding" | "signup", eligible: boolean) {
  const coordinator = useReaderPromptCoordinator();
  const owner = useId();
  const acquire = coordinator?.acquire;
  const release = coordinator?.release;
  const ready = coordinator?.ready;
  const activeOwner = coordinator?.activeOwner;
  const modalOpen = coordinator?.modalOpen;
  const paused = coordinator?.paused;
  useEffect(() => {
    if (!eligible) release?.(owner);
    else if (ready) acquire?.(kind, owner);
  }, [eligible, ready, activeOwner, modalOpen, paused, acquire, release, kind, owner]);
  useEffect(() => () => release?.(owner), [release, owner]);
  return eligible && (!coordinator || (ready && activeOwner === owner));
}
