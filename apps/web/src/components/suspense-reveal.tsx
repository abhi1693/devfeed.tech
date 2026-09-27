"use client";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { LoadingRevealFrame } from "./loading-reveal-frame";

function Ready({ onReady, children }: { onReady: (ready: boolean) => void; children: ReactNode }) {
  useEffect(() => {
    onReady(true);
    return () => onReady(false);
  }, [onReady]);
  return children;
}
/** The suspended tree must mount before we can reveal it. */
export function SuspenseReveal({
  fallback,
  children,
}: {
  fallback: ReactNode;
  children: ReactNode;
}) {
  const [ready, setReady] = useState(false);
  return (
    <LoadingRevealFrame loading={!ready} fallback={fallback}>
      <div
        className="reader-loading-content"
        style={ready ? undefined : { visibility: "hidden", height: 0, overflow: "hidden" }}
      >
        <Suspense fallback={null}>
          <Ready onReady={setReady}>{children}</Ready>
        </Suspense>
      </div>
    </LoadingRevealFrame>
  );
}
