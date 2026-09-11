"use client";

import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";
import { useThemePreferences } from "./theme-preferences";

export function ThemeToggle() {
  const { theme, save, loading, busy, unavailable, error } = useThemePreferences();
  return <div className="theme-toggle-container">
    <ThemeToggleButton theme={theme} onChange={save} disabled={loading || busy || unavailable} />
    {error && <span className="theme-toggle-error" role="alert">{error}</span>}
  </div>;
}
