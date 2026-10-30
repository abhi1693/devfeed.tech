"use client";

import type { ReactNode } from "react";
import { useUser } from "./user-account";
import { useReaderContext } from "./reader-context";

/** Public copy remains in guest HTML, then follows live account and preview state. */
export function GuestHomeVisibility({ children }: { children: ReactNode }) {
  const { user } = useUser();
  const { currentArticle } = useReaderContext();
  if (user) return null;
  // A preview keeps its background feed mounted. Keep the intro's space too,
  // so dismissing the dialog restores the same article and scroll position.
  return (
    <div
      aria-hidden={currentArticle ? true : undefined}
      inert={Boolean(currentArticle)}
      style={{ visibility: currentArticle ? "hidden" : undefined }}
    >
      {children}
    </div>
  );
}
