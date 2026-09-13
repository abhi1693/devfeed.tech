"use client";

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { animateReader } from "@/lib/reader-motion";

// Only the immediately preceding user navigation survives a route remount.
let pending: { scope: string; x: number; width: number; at: number } | undefined;

export function ReaderTabs({
  scope,
  selected,
  children,
}: {
  scope: string;
  selected: string;
  children: ReactNode;
}) {
  const nav = useRef<HTMLElement>(null);
  const indicator = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const element = nav.current!;
    let animation: Animation | null = null;
    const update = (transition = false) => {
      const active = element.querySelector<HTMLElement>('[aria-current="page"]');
      const pill = indicator.current;
      if (!active || !pill) return;
      const x = active.offsetLeft;
      const width = active.offsetWidth;
      pill.style.width = `${width}px`;
      pill.style.transform = `translateX(${x}px)`;
      element.dataset.indicator = "ready";
      const from = pending;
      if (transition) pending = undefined;
      if (transition && from?.scope === scope && Date.now() - from.at < 5000) {
        animation = animateReader(
          pill,
          [
            { transform: `translateX(${from.x}px)`, width: `${from.width}px` },
            { transform: `translateX(${x}px)`, width: `${width}px` },
          ],
          { duration: 180 },
        );
      }
    };
    update(true);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => update());
    observer?.observe(element);
    element.querySelectorAll("a").forEach((link) => observer?.observe(link));
    return () => {
      observer?.disconnect();
      animation?.cancel();
    };
  }, [scope, selected, children]);
  return (
    <nav
      ref={nav}
      className="feed-tabs reader-tabs"
      aria-label="Article type"
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
          return;
        if (!(event.target as Element).closest("a")) return;
        const active = nav.current?.querySelector<HTMLElement>('[aria-current="page"]');
        if (active)
          pending = { scope, x: active.offsetLeft, width: active.offsetWidth, at: Date.now() };
      }}
    >
      <span ref={indicator} className="reader-tab-indicator" aria-hidden="true" />
      {children}
    </nav>
  );
}
