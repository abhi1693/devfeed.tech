"use client";

import { Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

const changeEvent = "devfeed:sidebar-state-change";
const transientPreferences = new Map<string, boolean>();

function readPreference(key: string) {
  if (transientPreferences.has(key)) return transientPreferences.get(key)!;
  try {
    return window.localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

function writePreference(key: string, expanded: boolean) {
  try {
    window.localStorage.setItem(key, String(expanded));
    if (window.localStorage.getItem(key) === String(expanded)) transientPreferences.delete(key);
    else transientPreferences.set(key, expanded);
  } catch {
    transientPreferences.set(key, expanded);
  }
  window.dispatchEvent(new Event(changeEvent));
}

function subscribe(key: string, onChange: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== key) return;
    transientPreferences.delete(key);
    onChange();
  };
  window.addEventListener(changeEvent, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(changeEvent, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Reader-style navigation rail; callers keep their own routes, links and access rules. */
export function CollapsibleSidebar({
  id,
  label,
  storageKey,
  variant,
  syncDocumentState = false,
  children,
  footer,
}: {
  id: string;
  label: string;
  storageKey: string;
  variant: "reader" | "workspace";
  syncDocumentState?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const subscribeToPreference = useCallback(
    (onChange: () => void) => subscribe(storageKey, onChange),
    [storageKey],
  );
  const expanded = useSyncExternalStore(
    subscribeToPreference,
    () => readPreference(storageKey),
    () => false,
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobileToggle = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  useEffect(() => {
    if (variant !== "workspace" || !sidebar.current) return;
    const node = sidebar.current;
    let frame = 0;
    let previousTop = -1;
    const measure = () => {
      frame = 0;
      if (window.innerWidth < 1024) return;
      const top = Math.max(0, Math.min(window.innerHeight, node.getBoundingClientRect().top));
      if (top !== previousTop) {
        node.style.setProperty("--sidebar-top-offset", `${top}px`);
        previousTop = top;
      }
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const header = node.parentElement?.previousElementSibling;
    const observer =
      typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(schedule);
    if (header) observer?.observe(header);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer?.disconnect();
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [variant]);
  useEffect(() => {
    if (syncDocumentState)
      document.documentElement.dataset.sidebarState = expanded ? "expanded" : "collapsed";
  }, [expanded, syncDocumentState]);
  useEffect(() => {
    if (!mobileOpen) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      setMobileOpen(false);
      mobileToggle.current?.focus();
    };
    const onResize = () => {
      if (window.innerWidth >= 1024) setMobileOpen(false);
    };
    document.addEventListener("keydown", onEscape);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("keydown", onEscape);
      window.removeEventListener("resize", onResize);
    };
  }, [mobileOpen]);
  return (
    <>
      {variant === "workspace" && (
        <div className="sidebar-mobile-controls">
          <button
            ref={mobileToggle}
            type="button"
            className="sidebar-mobile-toggle"
            aria-expanded={mobileOpen}
            aria-controls={id}
            onClick={() => setMobileOpen((value) => !value)}
          >
            {mobileOpen ? <X size={16} aria-hidden /> : <Menu size={16} aria-hidden />}
            Navigation
          </button>
        </div>
      )}
      <aside
        ref={sidebar}
        id={id}
        aria-label={label}
        className="sidebar shared-sidebar"
        data-sidebar-layout={variant}
        data-expanded={expanded}
        data-mobile-open={mobileOpen}
        onClickCapture={(event) => {
          if (event.target instanceof Element && event.target.closest("a[href]"))
            setMobileOpen(false);
        }}
      >
        <div className="shared-sidebar-content">
          {children}
          {footer}
        </div>
        <div className="sidebar-toggle-area">
          <button
            type="button"
            className="sidebar-toggle"
            aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
            aria-pressed={expanded}
            aria-expanded={expanded}
            aria-controls={id}
            title={expanded ? "Collapse sidebar" : "Expand sidebar"}
            onClick={() => writePreference(storageKey, !expanded)}
          >
            {expanded ? (
              <PanelLeftClose size={18} aria-hidden />
            ) : (
              <PanelLeftOpen size={18} aria-hidden />
            )}
            <span className="sidebar-toggle-label">{expanded ? "Collapse" : "Expand"}</span>
          </button>
        </div>
      </aside>
    </>
  );
}
