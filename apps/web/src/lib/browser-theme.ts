"use client";

import { useSyncExternalStore } from "react";
import type { ThemeMode } from "@devfeed/ui/theme-toggle";

const storageKey = "devfeed:theme";
let selected: ThemeMode | null = null;
const listeners = new Set<() => void>();
const valid = (value: string | null): ThemeMode =>
  value === "light" || value === "dark" ? value : "system";
function snapshot(): ThemeMode {
  if (selected) return selected;
  try {
    return valid(localStorage.getItem(storageKey));
  } catch {
    return "system";
  }
}
function apply(theme: ThemeMode) {
  document.documentElement.classList.toggle(
    "dark",
    theme === "dark" ||
      (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches),
  );
}
export function setBrowserTheme(theme: ThemeMode) {
  selected = theme;
  try {
    localStorage.setItem(storageKey, theme);
  } catch {
    /* Keep this page usable without storage. */
  }
  apply(theme);
  listeners.forEach((listener) => listener());
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const sync = () => apply(snapshot());
  const storage = (event: StorageEvent) => {
    if (event.key !== storageKey && event.key !== null) return;
    selected = valid(event.newValue);
    sync();
    listener();
  };
  sync();
  system.addEventListener("change", sync);
  window.addEventListener("storage", storage);
  return () => {
    listeners.delete(listener);
    system.removeEventListener("change", sync);
    window.removeEventListener("storage", storage);
    if (!listeners.size) selected = null;
  };
}
export function useBrowserTheme() {
  return useSyncExternalStore(subscribe, snapshot, () => "system" as ThemeMode);
}
