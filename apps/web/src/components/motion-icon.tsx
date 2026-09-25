"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { Check, LoaderCircle } from "lucide-react";
import { animateReader } from "@/lib/reader-motion";

export function MotionIcon({ value, children }: { value: string; children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  const previous = useRef(value);
  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    const animation = animateReader(ref.current, [
      { opacity: 0, transform: "scale(.8) rotate(-12deg)" },
      { opacity: 1, transform: "scale(1) rotate(0)" },
    ]);
    return () => animation?.cancel();
  }, [value]);
  return (
    <span ref={ref} className="reader-motion-icon" aria-hidden="true">
      {children}
    </span>
  );
}

export function SaveFeedback({
  busy,
  saved,
  idle = null,
}: {
  busy: boolean;
  saved: boolean;
  idle?: ReactNode;
}) {
  return (
    <MotionIcon value={busy ? "saving" : saved ? "saved" : "idle"}>
      {busy ? (
        <LoaderCircle size={16} className="settings-spinner" />
      ) : saved ? (
        <Check size={16} />
      ) : (
        idle
      )}
    </MotionIcon>
  );
}
