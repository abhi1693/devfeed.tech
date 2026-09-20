"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";

const storageKey = "devfeed:sidebar-expanded";
const changeEvent = "devfeed:sidebar-state-change";

function subscribe(onStoreChange: () => void) {
  window.addEventListener(changeEvent, onStoreChange);
  window.addEventListener("storage", onStoreChange);
  return () => {
    window.removeEventListener(changeEvent, onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

function getSnapshot() {
  return window.localStorage.getItem(storageKey) === "true";
}

function getServerSnapshot() {
  return false;
}

export function SidebarToggle() {
  const expanded = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    document.documentElement.dataset.sidebarState = expanded ? "expanded" : "collapsed";
  }, [expanded]);

  function toggle() {
    const next = !expanded;
    window.localStorage.setItem(storageKey, String(next));
    window.dispatchEvent(new Event(changeEvent));
  }

  return (
    <button
      type="button"
      className="sidebar-toggle"
      aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
      aria-pressed={expanded}
      title={expanded ? "Collapse sidebar" : "Expand sidebar"}
      onClick={toggle}
    >
      {expanded ? (
        <PanelLeftClose size={18} aria-hidden="true" />
      ) : (
        <PanelLeftOpen size={18} aria-hidden="true" />
      )}
      <span className="sidebar-toggle-label">{expanded ? "Collapse" : "Expand"}</span>
    </button>
  );
}
