"use client";

import { useEffect } from "react";
import { returnToLogin } from "@/lib/api/client";

export function SessionLifetime({ expiresAt }: { expiresAt: number }) {
  useEffect(() => {
    const timer = window.setTimeout(returnToLogin, Math.max(0, expiresAt * 1000 - Date.now()));
    const restored = (event: PageTransitionEvent) => {
      // Reauthorize restored private pages after browser back/forward caching.
      if (event.persisted) window.location.reload();
    };
    window.addEventListener("pageshow", restored);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pageshow", restored);
    };
  }, [expiresAt]);
  return null;
}
