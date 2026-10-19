"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";
import { pipelineStages, type PipelineStage } from "./article-pipeline";

export const pipelineColumnsStorageKey = "devfeed:admin-article-pipeline-columns";
const changeEvent = "devfeed:admin-article-pipeline-columns-change";
let transient: string | undefined;

type Preferences = { hidden: PipelineStage[]; collapsed: PipelineStage[] };
const defaults: Preferences = { hidden: [], collapsed: [] };

function parse(snapshot: string | null): Preferences {
  try {
    const value: unknown = snapshot === null ? null : JSON.parse(snapshot);
    if (typeof value !== "object" || value === null || Array.isArray(value)) return defaults;
    const { hidden, collapsed } = value as Record<string, unknown>;
    if (!Array.isArray(hidden) || !Array.isArray(collapsed)) return defaults;
    const validHidden = pipelineStages.filter((stage) => hidden.includes(stage));
    return {
      hidden: validHidden.length === pipelineStages.length ? [] : validHidden,
      collapsed: pipelineStages.filter((stage) => collapsed.includes(stage)),
    };
  } catch {
    return defaults;
  }
}

function snapshot(): string | null {
  if (transient !== undefined) return transient;
  try {
    return window.localStorage.getItem(pipelineColumnsStorageKey);
  } catch {
    return null;
  }
}

function write(preferences: Preferences) {
  const value = JSON.stringify(preferences);
  try {
    window.localStorage.setItem(pipelineColumnsStorageKey, value);
    transient =
      window.localStorage.getItem(pipelineColumnsStorageKey) === value ? undefined : value;
  } catch {
    transient = value;
  }
  window.dispatchEvent(new Event(changeEvent));
}

function subscribe(onChange: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== pipelineColumnsStorageKey) return;
    transient = undefined;
    onChange();
  };
  window.addEventListener(changeEvent, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(changeEvent, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

const serverSnapshot = () => null;

export function usePipelineColumns() {
  const stored = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  const preferences = useMemo(() => parse(stored), [stored]);
  const visible = useMemo(
    () => pipelineStages.filter((stage) => !preferences.hidden.includes(stage)),
    [preferences],
  );
  const setVisible = useCallback((stage: PipelineStage, show: boolean) => {
    const current = parse(snapshot());
    if (
      !show &&
      !current.hidden.includes(stage) &&
      current.hidden.length === pipelineStages.length - 1
    )
      return;
    write({
      ...current,
      hidden: pipelineStages.filter((value) =>
        value === stage ? !show : current.hidden.includes(value),
      ),
    });
  }, []);
  const toggleCollapsed = useCallback((stage: PipelineStage) => {
    const current = parse(snapshot());
    write({
      ...current,
      collapsed: pipelineStages.filter((value) =>
        value === stage ? !current.collapsed.includes(stage) : current.collapsed.includes(value),
      ),
    });
  }, []);
  const showAll = useCallback(() => write({ ...parse(snapshot()), hidden: [] }), []);
  return { visible, collapsed: preferences.collapsed, setVisible, toggleCollapsed, showAll };
}
