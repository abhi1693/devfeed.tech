"use client";

import { useEffect, useRef } from "react";
import { notify } from "@/lib/notifications";

export function LoginFeedback({ error, signedOut }: { error?: string; signedOut: boolean }) {
  const shown = useRef<string | undefined>(undefined);
  useEffect(() => {
    const message = error || (signedOut ? "signed-out" : undefined);
    if (!message || shown.current === message) return;
    // Allow the host's passive subscription to attach on initial hydration.
    // Cleanup also prevents stale/duplicate messages during Strict Mode replay.
    const timer = setTimeout(() => {
      shown.current = message;
      if (error) notify.error("Sign-in failed", { description: error, id: "auth-result" });
      else notify.info("You have signed out of DevFeed.", { id: "auth-result" });
    }, 0);
    return () => clearTimeout(timer);
  }, [error, signedOut]);
  return null;
}
