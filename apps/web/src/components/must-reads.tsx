"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Gem, X, Check, Bookmark } from "lucide-react";
import { useReaderContext } from "./reader-context";
import { useUser } from "./user-account";
import { userRequest } from "@/lib/user";
import { claimMustReadsPresentation, setArticleBookmark } from "@/lib/user-mutations";
import { openMustReadsEvent } from "@/lib/reading-streak";
import type { Article } from "@/lib/types";
import { ArticleCard } from "./article-card";
import { EngagementProvider, bookmarkChanged } from "./article-engagement";
import styles from "./must-reads.module.css";

type Selection = {
  date: string;
  timezone: string;
  items: Article[];
  reasons: Record<string, string>;
  read_ids: string[];
  presented: boolean;
  preparing: boolean;
};
type Presentation = { mode: "manual" } | { mode: "automatic"; date: string };

export function MustReads() {
  const { user } = useUser();
  return user ? <DailyMustReads key={user.user_id} /> : null;
}

function DailyMustReads() {
  const { user, loading } = useUser();
  const { currentArticle, promptEligibility } = useReaderContext();
  const automaticAllowed = !loading && promptEligibility.mustReads && !currentArticle;
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [selection, setSelection] = useState<Selection>();
  const [presentation, setPresentation] = useState<Presentation>();
  const open = Boolean(presentation);
  const manualPresentation = presentation?.mode === "manual";
  const shown = open && !loading && (manualPresentation || automaticAllowed);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [retry, setRetry] = useState(0);
  const [pendingPresentation, setPendingPresentation] = useState<string>();
  const busy = useRef(false);
  const manualRequested = useRef(false);
  const loadedDay = useRef<string | undefined>(undefined);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const latestEligibility = useRef(automaticAllowed);
  const latestLoading = useRef(loading);
  const previousArticle = useRef(currentArticle?.id);
  const mutations = useRef<AbortController | null>(null);
  if (presentation?.mode === "automatic" && !automaticAllowed) {
    setPresentation(undefined);
    setPendingPresentation(presentation.date);
  }
  useEffect(() => {
    const controller = new AbortController();
    mutations.current = controller;
    return () => controller.abort();
  }, []);
  useEffect(() => {
    latestEligibility.current = automaticAllowed;
    latestLoading.current = loading;
  }, [automaticAllowed, loading]);
  useEffect(() => {
    if (currentArticle && currentArticle.id !== previousArticle.current) {
      manualRequested.current = false;
      setPresentation(undefined);
    }
    previousArticle.current = currentArticle?.id;
  }, [currentArticle]);
  useEffect(() => {
    if (loading) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      let preparing = true;
      if (document.visibilityState !== "visible") return;
      try {
        const value = await userRequest<Selection>(
          `must-reads?timezone=${encodeURIComponent(timezone)}`,
        );
        if (disposed) return;
        if (loadedDay.current !== value.date) setSaved(false);
        loadedDay.current = value.date;
        preparing = value.preparing;
        setSelection(value);
        setError(false);
      } catch {
        if (!disposed) setError(true);
      }
      if (!disposed) {
        const now = new Date();
        const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
        const delay =
          loadedDay.current && !preparing
            ? Math.max(1000, midnight.getTime() - now.getTime() + 1000)
            : 60_000;
        timer = setTimeout(() => void load(), delay);
      }
    };
    const focus = () => {
      clearTimeout(timer);
      void load();
    };
    void load();
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => {
      disposed = true;
      clearTimeout(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    };
  }, [timezone, retry, loading]);
  useEffect(() => {
    if (!selection?.items.length || open || !automaticAllowed) return;
    const pending = pendingPresentation === selection.date;
    if (selection.presented && !pending) return;
    const eligible = () =>
      document.visibilityState === "visible" &&
      latestEligibility.current &&
      !document.querySelector('dialog[open], [role="dialog"], [data-state="open"][role="menu"]');
    const presentPending = () => {
      if (!pending || !eligible()) return false;
      setPendingPresentation(undefined);
      setPresentation({ mode: "automatic", date: selection.date });
      return true;
    };
    if (presentPending()) return;
    const timer = setInterval(() => {
      if (busy.current || !eligible() || presentPending()) return;
      busy.current = true;
      void claimMustReadsPresentation(
        { date: selection.date, timezone, automatic: true },
        { csrfToken: user!.csrf_token, signal: mutations.current?.signal },
      )
        .then(({ claimed }) => {
          // A newer day must never inherit an older request's presentation claim.
          if (loadedDay.current !== selection.date) return;
          setSelection((value) =>
            value?.date === selection.date ? { ...value, presented: true } : value,
          );
          if (claimed && !dialog.current?.open) setPendingPresentation(selection.date);
        })
        .catch(() => {})
        .finally(() => {
          busy.current = false;
        });
    }, 2500);
    return () => clearInterval(timer);
  }, [selection, open, timezone, user, pendingPresentation, automaticAllowed]);
  useEffect(() => {
    if (!shown) return;
    const node = dialog.current;
    const returnFocus = trigger.current;
    node?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      node?.close();
      document.body.style.overflow = previous;
      returnFocus?.focus();
    };
  }, [shown]);
  useEffect(() => {
    if (!shown || !manualPresentation || !manualRequested.current || !selection?.items.length)
      return;
    manualRequested.current = false;
    void claimMustReadsPresentation(
      { date: selection.date, timezone, automatic: false },
      { csrfToken: user!.csrf_token, signal: mutations.current?.signal },
    )
      .then(() => setSelection((value) => (value ? { ...value, presented: true } : value)))
      .catch(() => {});
  }, [shown, manualPresentation, selection, timezone, user]);
  const show = useCallback(() => {
    if (loading) return;
    setPendingPresentation(undefined);
    manualRequested.current = true;
    setPresentation({ mode: "manual" });
    setRetry((value) => value + 1);
  }, [loading]);
  useEffect(() => {
    window.addEventListener(openMustReadsEvent, show);
    return () => window.removeEventListener(openMustReadsEvent, show);
  }, [show]);
  async function saveUnread() {
    if (loading || !selection || saving) return;
    setSaving(true);
    setError(false);
    try {
      for (const article of selection.items.filter(
        (item) => !selection.read_ids.includes(item.id),
      )) {
        if (latestLoading.current || mutations.current?.signal.aborted) return;
        const result = await setArticleBookmark(article.id, true, {
          csrfToken: user!.csrf_token,
          signal: mutations.current?.signal,
        });
        window.dispatchEvent(
          new CustomEvent(bookmarkChanged, {
            detail: { ...result, owner: user!.user_id },
          }),
        );
      }
      setSaved(true);
    } catch {
      setError(true);
    } finally {
      setSaving(false);
    }
  }
  const read = selection?.items.filter((item) => selection.read_ids.includes(item.id)).length ?? 0;
  return (
    <>
      <button
        ref={trigger}
        className={styles.trigger}
        onClick={show}
        aria-label="Today’s Must Reads"
        title="Today’s Must Reads"
        disabled={loading}
      >
        <Gem size={19} aria-hidden="true" />
        {!!selection?.items.length && !selection.presented && <span className={styles.dot} />}
      </button>
      {shown && (
        <dialog
          ref={dialog}
          className={styles.modal}
          aria-labelledby="must-reads-title"
          onCancel={() => setPresentation(undefined)}
          onClick={(event) => {
            if (event.target === event.currentTarget) setPresentation(undefined);
          }}
        >
          <div className={styles.heading}>
            <div>
              <p className={styles.eyebrow}>
                <Gem size={14} /> SELECTED FOR YOU · {selection?.date ?? "TODAY"}
              </p>
              <h2 id="must-reads-title">Today’s Must Reads</h2>
            </div>
            <button
              autoFocus
              onClick={() => setPresentation(undefined)}
              aria-label="Close Must Reads"
            >
              <X size={22} />
            </button>
          </div>
          {error && (
            <p role="alert">
              Couldn’t complete that request.{" "}
              <button onClick={() => setRetry((value) => value + 1)}>Try again</button>
            </p>
          )}
          {!selection && !error && <p role="status">Preparing your daily selection…</p>}
          {selection && !selection.items.length && (
            <p role="status">
              {selection.preparing
                ? "Your personalized picks are being prepared. Check back shortly."
                : "Follow topics and sources to build your daily selection."}
            </p>
          )}
          {!!selection?.items.length && (
            <EngagementProvider articleIds={selection.items.map((item) => item.id)}>
              <div className={styles.grid}>
                {selection.items.map((article, index) => (
                  <div key={article.id} className={styles.pick}>
                    <div className={styles.rank}>
                      <span>0{index + 1}</span>
                      {selection.read_ids.includes(article.id) && (
                        <span>
                          <Check size={13} /> Read
                        </span>
                      )}
                    </div>
                    <ArticleCard
                      article={article}
                      recommendation={selection.reasons[article.id]}
                      onOpen={() => {
                        manualRequested.current = false;
                        setPresentation(undefined);
                      }}
                    />
                  </div>
                ))}
                <div className={styles.briefing}>
                  <Gem size={28} />
                  <h3>Your daily briefing</h3>
                  <p>
                    {selection.items.length} picks based on your interests, kept together for today.
                  </p>
                  <strong>
                    {read} of {selection.items.length} read
                  </strong>
                  <progress
                    value={read}
                    max={selection.items.length}
                    aria-label="Daily reading progress"
                  />
                  <button
                    onClick={() => void saveUnread()}
                    disabled={saving || saved || read === selection.items.length}
                  >
                    <Bookmark size={16} />
                    {saved ? "Unread picks saved" : saving ? "Saving…" : "Save unread picks"}
                  </button>
                  <small>Come back tomorrow for your next selection.</small>
                </div>
              </div>
            </EngagementProvider>
          )}
        </dialog>
      )}
    </>
  );
}
