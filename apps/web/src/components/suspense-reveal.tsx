"use client";
import { Suspense, useEffect, useState, type ReactNode } from "react";

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
  const [placeholder, setPlaceholder] = useState(true);
  useEffect(() => {
    if (!ready) return;
    const timer = setTimeout(() => setPlaceholder(false), 180);
    return () => clearTimeout(timer);
  }, [ready]);
  return (
    <div className="reader-loading-reveal" data-loading={!ready}>
      {(!ready || placeholder) && (
        <div className="reader-loading-placeholder" aria-hidden={ready} inert={ready}>
          {fallback}
        </div>
      )}
      <div
        className="reader-loading-content"
        style={ready ? undefined : { visibility: "hidden", height: 0, overflow: "hidden" }}
      >
        <Suspense fallback={null}>
          <Ready onReady={setReady}>{children}</Ready>
        </Suspense>
      </div>
    </div>
  );
}
