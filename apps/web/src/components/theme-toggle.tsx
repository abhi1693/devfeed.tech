"use client";

import { useEffect, useRef } from "react";
import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";

const storageKey = "devfeed:theme";
type Theme = "light" | "dark";
const preference = (value: string | null): Theme | null =>
  value === "light" || value === "dark" ? value : null;

export function ThemeToggle() {
  const selected = useRef<Theme | null>(null);

  useEffect(() => {
    const system = window.matchMedia("(prefers-color-scheme: dark)");
    try {
      selected.current = preference(localStorage.getItem(storageKey));
    } catch {
      // Theme switching still works when browser storage is unavailable.
    }
    const sync = () => {
      document.documentElement.classList.toggle("dark",
        (selected.current ?? (system.matches ? "dark" : "light")) === "dark");
    };
    const storageChanged = (event: StorageEvent) => {
      if (event.key !== storageKey && event.key !== null) return;
      selected.current = preference(event.newValue);
      sync();
    };
    sync();
    system.addEventListener("change", sync);
    window.addEventListener("storage", storageChanged);
    return () => {
      system.removeEventListener("change", sync);
      window.removeEventListener("storage", storageChanged);
    };
  }, []);

  function toggle() {
    const theme =
      document.documentElement.classList.contains("dark") ? "light" : "dark";
    selected.current = theme;
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem(storageKey, theme);
    } catch {
      // Keep the chosen theme for this page even if it cannot be persisted.
    }
  }

  return <ThemeToggleButton onClick={toggle} />;
}
