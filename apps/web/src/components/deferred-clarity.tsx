"use client";

import Clarity from "@microsoft/clarity";
import { useEffect } from "react";

export function DeferredClarity({ projectId }: { projectId: string }) {
  useEffect(() => {
    let loaded = false;

    const loadOnce = () => {
      if (loaded) return;
      loaded = true;
      try {
        Clarity.init(projectId);
      } catch {
        /* Tracking failure must never interrupt the reader. */
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") loadOnce();
    };
    const events: Array<keyof WindowEventMap> = ["pointerdown", "keydown", "scroll", "touchstart"];
    events.forEach((eventName) => {
      window.addEventListener(eventName, loadOnce, { once: true, passive: true });
    });
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("pagehide", loadOnce, { once: true });

    return () => {
      events.forEach((eventName) => window.removeEventListener(eventName, loadOnce));
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("pagehide", loadOnce);
    };
  }, [projectId]);

  return null;
}
