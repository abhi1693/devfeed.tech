"use client";

import { useEffect, useEffectEvent } from "react";
import { runWhenPageActive } from "@devfeed/ui/page-activity";

/** Quiet, sequential polling. Changing the interval cancels only automatic work. */
export function usePolling(
  load: (signal: AbortSignal) => Promise<unknown> | void,
  milliseconds: number,
  key = "",
) {
  const tick = useEffectEvent(load);
  useEffect(() => {
    if (milliseconds <= 0) return;
    return runWhenPageActive((signal, resumed) => {
      let timer: ReturnType<typeof setTimeout>;
      async function poll() {
        if (signal.aborted) return;
        try {
          await tick(signal);
        } finally {
          if (!signal.aborted) timer = setTimeout(() => void poll(), milliseconds);
        }
      }
      if (resumed) void poll();
      else timer = setTimeout(() => void poll(), milliseconds);
      return () => clearTimeout(timer);
    });
  }, [milliseconds, key]);
}
