"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, Sparkles, X } from "lucide-react";
import { useUser } from "./user-account";
import { DevCardArtwork } from "./dev-card-artwork";
import { InfiniteChoices } from "./infinite-choices";
import { readerLoginLink } from "@/lib/reader-runtime";
import { readDevCardDraft, saveDevCardDraft } from "@/lib/dev-card-draft";
import { safeExternalUrl } from "@/lib/feed-query";
import type { UserStack } from "@/lib/user";
import type { Topic } from "@/lib/types";
import { trackEvent } from "@/lib/analytics";
import styles from "./dev-card-promo.module.css";

const dismissedKey = "devfeed:dev-card-promo-dismissed";
function remembered(key: string) {
  try {
    return sessionStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}
function remember(key: string) {
  try {
    sessionStorage.setItem(key, "true");
  } catch {
    /* Still works for this mount. */
  }
}

export function DevCardPromo({ requested = false }: { requested?: boolean }) {
  const { user, loading, unavailable } = useUser();
  const [dismissed, setDismissed] = useState(true);
  const [revealed, setRevealed] = useState(false);
  const [play, setPlay] = useState(false);
  const [details, setDetails] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [stack, setStack] = useState<UserStack[]>([]);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [storageError, setStorageError] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const tilt = useRef<HTMLDivElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const id = useId();
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setDismissed(!requested && remembered(dismissedKey));
      const draft = readDevCardDraft();
      if (draft) {
        setName(draft.name);
        setStack(draft.stack);
        setPending(draft.ready);
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [requested]);
  const shown = !dismissed && !loading && (!user || pending);
  useEffect(() => {
    if (!shown) return;
    const element = dialog.current!;
    let previousFocus: HTMLElement | null = null;
    let previousOverflow = "";
    let opened = false;
    let timer: ReturnType<typeof setTimeout>;
    let detailsTimer: ReturnType<typeof setTimeout>;
    const open = () => {
      // Let welcome, article, and account dialogs finish before presenting this one.
      if (document.querySelector("dialog[open]")) {
        timer = setTimeout(open, 250);
        return;
      }
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      previousOverflow = document.body.style.overflow;
      element.showModal();
      document.body.style.overflow = "hidden";
      opened = true;
      const animate = window.matchMedia("(prefers-reduced-motion: no-preference)").matches;
      setPlay(animate);
      if (animate) detailsTimer = setTimeout(() => setDetails(true), 1900);
      else setDetails(true);
      setRevealed(true);
    };
    // Count time since page entry, including time spent on other client-side routes.
    timer = setTimeout(open, requested ? 0 : Math.max(1200, 30_000 - performance.now()));
    return () => {
      clearTimeout(timer);
      clearTimeout(detailsTimer);
      if (opened) {
        element.close();
        document.body.style.overflow = previousOverflow;
        if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
      }
    };
  }, [shown, requested]);
  function dismiss() {
    remember(dismissedKey);
    setDismissed(true);
  }
  if (!shown) return null;
  const personal = editing || !!name;
  return (
    <dialog
      ref={dialog}
      className={styles.modal}
      data-details={details}
      aria-label="Your dev card preview"
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not([disabled]), a[href], input:not([disabled]), [tabindex="0"]',
          ),
        ).filter((node) => node.getClientRects().length > 0 && !node.closest("[inert]"));
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
    >
      <button className={styles.dismiss} aria-label="Dismiss dev card preview" onClick={dismiss}>
        <X size={18} />
      </button>
      <section
        className={styles.promo}
        aria-label="Discover your dev card"
        data-revealed={revealed}
      >
        <div
          className={styles.stage}
          data-testid="dev-card-stage"
          onPointerMove={(event) => {
            if (
              event.pointerType !== "mouse" ||
              !window.matchMedia("(prefers-reduced-motion: no-preference)").matches
            )
              return;
            const rect = event.currentTarget.getBoundingClientRect();
            tilt.current?.style.setProperty(
              "--tilt-y",
              `${((event.clientX - rect.left) / rect.width - 0.5) * 14}deg`,
            );
            tilt.current?.style.setProperty(
              "--tilt-x",
              `${((event.clientY - rect.top) / rect.height - 0.5) * -10}deg`,
            );
          }}
          onPointerLeave={() => {
            tilt.current?.style.setProperty("--tilt-y", "0deg");
            tilt.current?.style.setProperty("--tilt-x", "0deg");
          }}
        >
          <div className={styles.aura} aria-hidden="true" />
          <div className={styles.float}>
            <div ref={tilt} className={styles.tilt}>
              <div className={styles.reveal} data-play={play}>
                <div className={styles.back} aria-hidden="true">
                  <Sparkles size={32} />
                  <span>DEVFEED</span>
                  <small>BUILT BY CURIOSITY</small>
                </div>
                <div className={styles.front}>
                  <DevCardArtwork
                    data={{
                      name: personal ? name.trim() || "Your name here" : "Alex Morgan",
                      initials: personal
                        ? name
                            .trim()
                            .split(/\s+/)
                            .slice(0, 2)
                            .map((word) => Array.from(word)[0] ?? "")
                            .join("")
                            .toUpperCase() || "YOU"
                        : "AM",
                      username: null,
                      avatar: null,
                      bio: personal ? "" : "Building things. Staying curious.",
                      location: null,
                      technologies: personal
                        ? stack.map((item) => ({
                            id: item.topic_id,
                            name: item.name,
                            kind: item.kind,
                            logoUrl: safeExternalUrl(item.logo_url) ?? null,
                          }))
                        : ["TypeScript", "React", "Python", "Rust"].map((name) => ({
                            id: name,
                            name,
                            kind: "technology",
                            logoUrl: null,
                          })),
                      stats: personal
                        ? []
                        : [
                            { label: "DAY STREAK", value: 7 },
                            { label: "BEST STREAK", value: 21 },
                            { label: "DAYS READING", value: 128 },
                          ],
                    }}
                  />
                  <div className={styles.shine} aria-hidden="true" />
                </div>
              </div>
            </div>
          </div>
          <span className={styles.caption}>
            {personal
              ? "Your preview · reading stats start with your account"
              : "Example card · sample reading stats"}
          </span>
        </div>
        <div className={styles.copy} inert={!details} aria-hidden={!details}>
          <span className={styles.eyebrow}>
            <Sparkles size={14} /> A LITTLE YOU. A LOT OF POSSIBILITY.
          </span>
          <h2>
            Your next favourite <br />
            card is yours.
          </h2>
          <p>
            Your stack. Your reading journey. <br />A dev card that grows with you.
          </p>
          {user ? (
            <Link className="button primary" href="/settings/profile">
              Finish your dev card <ArrowUpRight size={16} />
            </Link>
          ) : !editing ? (
            <>
              <button
                className="button primary"
                onClick={() => {
                  trackEvent("dev_card_preview_started", {});
                  setEditing(true);
                  requestAnimationFrame(() => nameInput.current?.focus());
                }}
              >
                Create your dev card <ArrowUpRight size={16} />
              </button>
              {!unavailable && <small>Preview it first. Create a free account to keep it.</small>}
            </>
          ) : (
            <div className={styles.editor}>
              <label htmlFor={`${id}-name`}>Your display name</label>
              <input
                ref={nameInput}
                id={`${id}-name`}
                autoComplete="nickname"
                maxLength={100}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Your name"
              />
              <label htmlFor={`${id}-stack`}>
                Your technologies <span>({stack.length}/4)</span>
              </label>
              <div className={styles.chips}>
                {stack.map((item) => (
                  <button
                    key={item.topic_id}
                    onClick={() =>
                      setStack(stack.filter((entry) => entry.topic_id !== item.topic_id))
                    }
                    aria-label={`Remove ${item.name}`}
                  >
                    {item.name}
                    <X size={12} />
                  </button>
                ))}
              </div>
              <input
                id={`${id}-stack`}
                type="search"
                placeholder="Find a technology…"
                maxLength={100}
                disabled={stack.length >= 4}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {query.trim() && stack.length < 4 && (
                <div className={styles.results}>
                  <InfiniteChoices<Topic> label="topics" query={query}>
                    {(topics) =>
                      topics
                        .filter((topic) => !stack.some((item) => item.topic_id === topic.id))
                        .map((topic) => (
                          <button
                            key={topic.id}
                            onClick={() => {
                              setStack([
                                ...stack,
                                {
                                  topic_id: topic.id,
                                  name: topic.name,
                                  slug: topic.slug,
                                  kind: topic.kind,
                                  logo_url: topic.logo_url,
                                  status: "active",
                                  section: "primary",
                                  since_year: null,
                                },
                              ]);
                              setQuery("");
                            }}
                            aria-label={`Add ${topic.name}`}
                          >
                            {topic.name}
                          </button>
                        ))
                    }
                  </InfiniteChoices>
                </div>
              )}
              {unavailable ? (
                <button className="button primary" disabled>
                  Save my dev card
                </button>
              ) : (
                <a
                  className="button primary"
                  {...readerLoginLink("/settings/profile", { register: true })}
                  onClick={(event) => {
                    if (
                      !saveDevCardDraft({
                        name: name.trim(),
                        stack,
                        ready: true,
                        created: Date.now(),
                      })
                    ) {
                      event.preventDefault();
                      setStorageError(true);
                      return;
                    }
                    trackEvent("dev_card_signup_started", {});
                    setPending(true);
                  }}
                >
                  Save my dev card <ArrowUpRight size={16} />
                </a>
              )}
              <small>
                {unavailable
                  ? "You can preview your card here. Account creation is temporarily unavailable; please try again later."
                  : "Create a free account to save and download your card."}
              </small>
              {storageError && (
                <p role="alert">
                  Your browser couldn’t keep this preview. Enable session storage and try again.
                </p>
              )}
            </div>
          )}
        </div>
      </section>
    </dialog>
  );
}
