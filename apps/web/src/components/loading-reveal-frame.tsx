"use client";
import { useEffect, useState, type ReactNode } from "react";

/** Own the placeholder transition; callers decide when and how content mounts. */
export function LoadingRevealFrame({
  loading,
  fallback,
  children,
}: {
  loading: boolean;
  fallback: ReactNode;
  children?: ReactNode;
}) {
  const [placeholder, setPlaceholder] = useState(loading);
  useEffect(() => {
    if (loading) return;
    const timer = setTimeout(() => setPlaceholder(false), 180);
    return () => clearTimeout(timer);
  }, [loading]);
  return (
    <div className="reader-loading-reveal" data-loading={loading}>
      {(loading || placeholder) && (
        <div className="reader-loading-placeholder" aria-hidden={!loading} inert={!loading}>
          {fallback}
        </div>
      )}
      {children}
    </div>
  );
}
