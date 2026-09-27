"use client";
import type { ReactNode } from "react";
import { LoadingRevealFrame } from "./loading-reveal-frame";

/** Mount content only after explicit loading completes. */
export function LoadingReveal({
  loading,
  fallback,
  children,
}: {
  loading: boolean;
  fallback: ReactNode;
  children?: ReactNode;
}) {
  return (
    <LoadingRevealFrame loading={loading} fallback={fallback}>
      {!loading && <div className="reader-loading-content">{children}</div>}
    </LoadingRevealFrame>
  );
}
