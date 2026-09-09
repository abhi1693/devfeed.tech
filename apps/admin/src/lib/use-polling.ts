"use client";

import { useEffect, useEffectEvent } from "react";

/** Quiet, sequential polling. Changing the interval cancels only automatic work. */
export function usePolling(load: (signal: AbortSignal) => Promise<unknown> | void, milliseconds: number, key = "") {
  const tick = useEffectEvent(load);
  useEffect(() => {
    if (milliseconds <= 0) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        if (document.visibilityState !== "hidden") await tick(abort.signal);
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(() => void poll(), milliseconds);
      }
    }
    timer = setTimeout(() => void poll(), milliseconds);
    return () => { abort.abort(); clearTimeout(timer); };
  }, [milliseconds, key]);
}
