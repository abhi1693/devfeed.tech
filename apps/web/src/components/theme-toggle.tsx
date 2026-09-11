"use client";

import { useEffect, useRef } from "react";
import { Moon, Sun } from "lucide-react";

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
      document.documentElement.dataset.theme =
        selected.current ?? (system.matches ? "dark" : "light");
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
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    selected.current = theme;
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(storageKey, theme);
    } catch {
      // Keep the chosen theme for this page even if it cannot be persisted.
    }
  }

  return (
    <button className="theme-toggle" type="button" onClick={toggle}>
      <span className="theme-to-dark" title="Switch to dark theme">
        <Moon size={19} aria-hidden="true" />
        <span className="sr-only">Switch to dark theme</span>
      </span>
      <span className="theme-to-light" title="Switch to light theme">
        <Sun size={19} aria-hidden="true" />
        <span className="sr-only">Switch to light theme</span>
      </span>
    </button>
  );
}
