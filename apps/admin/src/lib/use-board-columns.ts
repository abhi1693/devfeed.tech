"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";

const changeEvent = "devfeed:admin-board-columns-change";
const transient = new Map<string, string>();
type Preferences<Stage extends string> = { hidden: Stage[]; collapsed: Stage[] };

function parse<Stage extends string>(
  stored: string | null,
  stages: readonly Stage[],
): Preferences<Stage> {
  const defaults = { hidden: [], collapsed: [] };
  try {
    const value: unknown = stored === null ? null : JSON.parse(stored);
    if (typeof value !== "object" || value === null || Array.isArray(value)) return defaults;
    const { hidden, collapsed } = value as Record<string, unknown>;
    if (!Array.isArray(hidden) || !Array.isArray(collapsed)) return defaults;
    const validHidden = stages.filter((stage) => hidden.includes(stage));
    return {
      hidden: validHidden.length === stages.length ? [] : validHidden,
      collapsed: stages.filter((stage) => collapsed.includes(stage)),
    };
  } catch {
    return defaults;
  }
}

function snapshot(storageKey: string): string | null {
  const fallback = transient.get(storageKey);
  if (fallback !== undefined) return fallback;
  try {
    return window.localStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function write<Stage extends string>(storageKey: string, preferences: Preferences<Stage>) {
  const value = JSON.stringify(preferences);
  try {
    window.localStorage.setItem(storageKey, value);
    if (window.localStorage.getItem(storageKey) === value) transient.delete(storageKey);
    else transient.set(storageKey, value);
  } catch {
    transient.set(storageKey, value);
  }
  window.dispatchEvent(new CustomEvent(changeEvent, { detail: storageKey }));
}

const serverSnapshot = () => null;

/** Keep browser-local column choices isolated for each board. */
export function useBoardColumns<Stage extends string>(
  storageKey: string,
  stages: readonly Stage[],
) {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const onStorage = (event: StorageEvent) => {
        if (event.key !== null && event.key !== storageKey) return;
        transient.delete(storageKey);
        onChange();
      };
      const onLocal = (event: Event) => {
        if ((event as CustomEvent<string>).detail === storageKey) onChange();
      };
      window.addEventListener(changeEvent, onLocal);
      window.addEventListener("storage", onStorage);
      return () => {
        window.removeEventListener(changeEvent, onLocal);
        window.removeEventListener("storage", onStorage);
      };
    },
    [storageKey],
  );
  const getSnapshot = useCallback(() => snapshot(storageKey), [storageKey]);
  const stored = useSyncExternalStore(subscribe, getSnapshot, serverSnapshot);
  const preferences = useMemo(() => parse(stored, stages), [stored, stages]);
  const visible = useMemo(
    () => stages.filter((stage) => !preferences.hidden.includes(stage)),
    [stages, preferences],
  );
  const setVisible = useCallback(
    (stage: Stage, show: boolean) => {
      const current = parse(snapshot(storageKey), stages);
      if (!show && !current.hidden.includes(stage) && current.hidden.length === stages.length - 1)
        return;
      write(storageKey, {
        ...current,
        hidden: stages.filter((value) =>
          value === stage ? !show : current.hidden.includes(value),
        ),
      });
    },
    [storageKey, stages],
  );
  const toggleCollapsed = useCallback(
    (stage: Stage) => {
      const current = parse(snapshot(storageKey), stages);
      write(storageKey, {
        ...current,
        collapsed: stages.filter((value) =>
          value === stage ? !current.collapsed.includes(stage) : current.collapsed.includes(value),
        ),
      });
    },
    [storageKey, stages],
  );
  const showAll = useCallback(
    () => write(storageKey, { ...parse(snapshot(storageKey), stages), hidden: [] }),
    [storageKey, stages],
  );
  return { visible, collapsed: preferences.collapsed, setVisible, toggleCollapsed, showAll };
}
