"use client";
import { useEffect, useRef } from "react";
import { animateReader } from "@/lib/reader-motion";

import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";
import { useThemePreferences } from "./theme-preferences";

export function ThemeToggle() {
  const { theme, save, loading, busy, unavailable, error } = useThemePreferences();
  const ref = useRef<HTMLDivElement>(null);
  const previous = useRef(theme);
  useEffect(() => {
    if (previous.current === theme) return;
    previous.current = theme;
    const animation = animateReader(ref.current?.querySelector("svg") ?? null, [
      { opacity: 0, transform: "rotate(-30deg) scale(.8)" },
      { opacity: 1, transform: "rotate(0) scale(1)" },
    ]);
    return () => animation?.cancel();
  }, [theme]);
  return (
    <div ref={ref} className="theme-toggle-container">
      <ThemeToggleButton theme={theme} onChange={save} disabled={loading || busy || unavailable} />
      {error && (
        <span className="theme-toggle-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
