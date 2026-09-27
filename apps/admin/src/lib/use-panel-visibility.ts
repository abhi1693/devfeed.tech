"use client";

import { useEffect, useRef, useState } from "react";

/** Preload one short scroll ahead; poll only while the panel is actually visible. */
export function usePanelVisibility() {
  const ref = useRef<HTMLElement>(null);
  const [near, setNear] = useState(false);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      let cancelled = false;
      queueMicrotask(() => {
        if (!cancelled) {
          setNear(true);
          setVisible(true);
        }
      });
      return () => {
        cancelled = true;
      };
    }
    const nearby = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), {
      rootMargin: "300px 0px",
    });
    const viewport = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    nearby.observe(element);
    viewport.observe(element);
    return () => {
      nearby.disconnect();
      viewport.disconnect();
    };
  }, []);
  return { ref, near, visible };
}
