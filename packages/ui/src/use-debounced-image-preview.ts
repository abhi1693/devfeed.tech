"use client";
import { useEffect, useState } from "react";

/** Accept an already validated URL; hide stale previews until typing settles. */
export function useDebouncedImagePreview(url: string | null, delay = 300) {
  const [settledUrl, setSettledUrl] = useState(url);
  useEffect(() => {
    const timer = setTimeout(() => setSettledUrl(url), delay);
    return () => clearTimeout(timer);
  }, [url, delay]);
  return settledUrl === url ? url : null;
}
