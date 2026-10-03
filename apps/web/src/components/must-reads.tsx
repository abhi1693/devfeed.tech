"use client";

import { useEffect, useRef, useState } from "react";
import { Gem, X, Check, Bookmark } from "lucide-react";
import { usePathname } from "next/navigation";
import { useUser } from "./user-account";
import { userRequest } from "@/lib/user";
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

export function MustReads() {
  const { user } = useUser();
  return user ? <DailyMustReads key={user.user_id} /> : null;
}

function DailyMustReads() {
  const { user } = useUser();
  const pathname = usePathname();
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [selection, setSelection] = useState<Selection>();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [retry, setRetry] = useState(0);
  const [pendingPresentation, setPendingPresentation] = useState<string>();
  const busy = useRef(false);
  const manualRequested = useRef(false);
  const loadedDay = useRef<string | undefined>(undefined);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const latestPath = useRef(pathname);
  useEffect(() => {
    latestPath.current = pathname;
  }, [pathname]);
  useEffect(() => {
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
  }, [timezone, retry]);
  useEffect(() => {
    if (!selection?.items.length || open) return;
    const pending = pendingPresentation === selection.date;
    if (selection.presented && !pending) return;
    const eligible = () =>
      document.visibilityState === "visible" &&
      !latestPath.current?.startsWith("/articles/") &&
      !document.querySelector('dialog[open], [role="dialog"], [data-state="open"][role="menu"]');
    const presentPending = () => {
      if (!pending || !eligible()) return false;
      setPendingPresentation(undefined);
      setOpen(true);
      return true;
    };
    if (presentPending()) return;
    const timer = setInterval(() => {
      if (busy.current || !eligible() || presentPending()) return;
      busy.current = true;
      void userRequest<{ claimed: boolean }>("must-reads/presentation", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": user!.csrf_token },
        body: JSON.stringify({ date: selection.date, timezone, automatic: true }),
      })
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
  }, [selection, open, timezone, user, pendingPresentation]);
  useEffect(() => {
    if (!open) return;
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
  }, [open]);
  useEffect(() => {
    if (!open || !manualRequested.current || !selection?.items.length) return;
    manualRequested.current = false;
    void userRequest("must-reads/presentation", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": user!.csrf_token },
      body: JSON.stringify({ date: selection.date, timezone, automatic: false }),
    })
      .then(() => setSelection((value) => (value ? { ...value, presented: true } : value)))
      .catch(() => {});
  }, [open, selection, timezone, user]);
  function show() {
    setPendingPresentation(undefined);
    manualRequested.current = true;
    setOpen(true);
    setRetry((value) => value + 1);
  }
  async function saveUnread() {
    if (!selection || saving) return;
    setSaving(true);
    setError(false);
    try {
      for (const article of selection.items.filter(
        (item) => !selection.read_ids.includes(item.id),
      )) {
        await userRequest(`articles/${article.id}/bookmark`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user!.csrf_token },
          body: JSON.stringify({ bookmarked: true }),
        });
        window.dispatchEvent(
          new CustomEvent(bookmarkChanged, {
            detail: { article_id: article.id, bookmarked: true, owner: user!.user_id },
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
      >
        <Gem size={19} aria-hidden="true" />
        {!!selection?.items.length && !selection.presented && <span className={styles.dot} />}
      </button>
      {open && (
        <dialog
          ref={dialog}
          className={styles.modal}
          aria-labelledby="must-reads-title"
          onCancel={() => setOpen(false)}
          onClick={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <div className={styles.heading}>
            <div>
              <p className={styles.eyebrow}>
                <Gem size={14} /> SELECTED FOR YOU · {selection?.date ?? "TODAY"}
              </p>
              <h2 id="must-reads-title">Today’s Must Reads</h2>
            </div>
            <button autoFocus onClick={() => setOpen(false)} aria-label="Close Must Reads">
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
              <div
                className={styles.grid}
                onClickCapture={(event) => {
                  if ((event.target as HTMLElement).closest('a[href*="/articles/"]'))
                    setOpen(false);
                }}
              >
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
                    <ArticleCard article={article} recommendation={selection.reasons[article.id]} />
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
