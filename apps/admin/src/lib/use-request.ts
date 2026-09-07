"use client";
import { useEffect, useRef, useState } from "react";
import { notifyFailure } from "./notifications";
import { ApiError, returnToLogin } from "./api/client";
export function useRequest<T>(key: string, load: (signal: AbortSignal) => Promise<T>, pollInterval = 0) {
  const [state, setState] = useState<{ key: string; data?: T; error?: Error }>();
  const failed = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      try {
        const data = await load(abort.signal);
        if (!abort.signal.aborted) { failed.current = false; setState({ key, data }); }
      } catch (error) {
        if (abort.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) { returnToLogin(); return; }
        // Multiple related panels can fail together. Share a toast ID, and keep
        // failed searches/retries quiet until this reader has recovered.
        if (!failed.current) notifyFailure(error, "Could not load data", "admin-read-error");
        failed.current = true;
        setState(previous => ({ key, data: previous?.key === key ? previous.data : undefined, error: error instanceof Error ? error : new Error("Request failed") }));
      }
      if (!abort.signal.aborted && pollInterval > 0) timer = setTimeout(poll, pollInterval);
    }
    function poll() {
      if (document.visibilityState === "hidden") { timer = setTimeout(poll, pollInterval); return; }
      void refresh();
    }
    void refresh();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [key, load, pollInterval]);
  return state?.key === key ? { ...state, loading: false } : { data: undefined, error: undefined, loading: true };
}
