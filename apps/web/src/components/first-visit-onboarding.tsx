"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Bookmark, Compass, Heart, X, Zap } from "lucide-react";
import Link from "next/link";
import { useUser } from "./user-account";
import styles from "./first-visit-onboarding.module.css";

const seenKey = "devfeed:first-visit-onboarding-seen";
let shownThisSession = false;

const steps = [
  {
    icon: Zap,
    eyebrow: "YOUR DAILY EDGE",
    title: "DevFeed is your daily briefing on what’s next.",
    description:
      "It brings developer news, launches, tutorials, and practical lessons into one focused feed—so you can spend ten minutes reading and leave knowing what is changing and what is worth trying.",
  },
  {
    icon: Compass,
    eyebrow: "FIND YOUR NEXT RABBIT HOLE",
    title: "Discover what to learn and build next.",
    description:
      "Explore programming languages, libraries, tools, and ideas from the sources you trust. Follow what makes you curious and let DevFeed bring the useful stuff closer.",
  },
  {
    icon: Heart,
    eyebrow: "LEARN FROM PEOPLE WHO BUILD",
    title: "See how other developers turn ideas into working software.",
    description:
      "Find practical write-ups, sharp lessons, and projects being shipped in the real world—not just headlines repeating the same announcement.",
  },
  {
    icon: Bookmark,
    eyebrow: "MAKE YOUR CURIOSITY USEFUL",
    title: "Turn one good read into your next move.",
    description:
      "Sign up to turn your follows, likes, and saves into a personalized feed—one that gets more useful as it learns what you care about. Start exploring now; create your account when you’re ready.",
  },
] as const;

export function FirstVisitOnboarding() {
  const { user, loading, unavailable } = useUser();
  const dialog = useRef<HTMLDialogElement>(null);
  const [step, setStep] = useState(0);
  const [visible, setVisible] = useState(false);
  const current = steps[step];
  const Icon = current.icon;

  useEffect(() => {
    if (
      shownThisSession ||
      loading ||
      unavailable ||
      user ||
      window.location.pathname !== "/latest"
    )
      return;
    try {
      if (localStorage.getItem(seenKey)) return;
    } catch {
      // Continue when storage is unavailable; this visit can still be useful.
    }
    shownThisSession = true;
    const timer = window.setTimeout(() => setVisible(true), 0);
    try {
      localStorage.setItem(seenKey, "1");
    } catch {
      // The module-level flag keeps the tour from repeating in this visit.
    }
    return () => window.clearTimeout(timer);
  }, [loading, unavailable, user]);

  useEffect(() => {
    if (!visible || !dialog.current) return;
    dialog.current.showModal();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") setStep((value) => Math.min(value + 1, steps.length - 1));
      if (event.key === "ArrowLeft") setStep((value) => Math.max(value - 1, 0));
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [visible]);

  useEffect(() => {
    if (!visible || step === steps.length - 1) return;
    const timer = window.setInterval(() => {
      setStep((value) => Math.min(value + 1, steps.length - 1));
    }, 6000);
    return () => window.clearInterval(timer);
  }, [step, visible]);

  function dismiss() {
    dialog.current?.close();
    setVisible(false);
  }

  function next() {
    if (step === steps.length - 1) {
      dismiss();
      return;
    }
    setStep((value) => value + 1);
  }

  if (!visible) return null;

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="first-visit-onboarding-title"
      aria-describedby="first-visit-onboarding-description"
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
    >
      <div className={styles.shell}>
        <div key={`visual-${step}`} className={styles.visual} aria-hidden="true">
          <div className={styles.visualOrb}>
            <div className={styles.visualIcon}>
              <Icon size={36} strokeWidth={1.8} />
            </div>
            <span className={`${styles.orbit} ${styles.orbitOne}`} />
            <span className={`${styles.orbit} ${styles.orbitTwo}`} />
          </div>
          <div className={styles.signalCard}>
            <span>DEVFEED / SIGNAL</span>
            <strong>{current.eyebrow.toLowerCase()}</strong>
            <i />
            <i />
            <i />
          </div>
        </div>
        <div className={styles.content}>
          <button className={styles.close} aria-label="Close introduction" onClick={dismiss}>
            <X size={20} />
          </button>
          <div className={styles.progress} aria-label={`Step ${step + 1} of ${steps.length}`}>
            {steps.map((item, index) => (
              <span key={item.eyebrow} className={index === step ? styles.activeDot : undefined} />
            ))}
          </div>
          <div key={`copy-${step}`} className={styles.slideCopy} aria-live="polite">
            <h2 id="first-visit-onboarding-title">{current.title}</h2>
            <p id="first-visit-onboarding-description" className={styles.description}>
              {current.description}
            </p>
          </div>
          <div className={styles.footer}>
            {step === steps.length - 1 ? (
              <Link className="button primary" href="/latest" onClick={dismiss}>
                Start reading <ArrowRight size={17} aria-hidden="true" />
              </Link>
            ) : (
              <button key={step} className={`button primary ${styles.nextButton}`} onClick={next}>
                Next <ArrowRight size={17} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}
