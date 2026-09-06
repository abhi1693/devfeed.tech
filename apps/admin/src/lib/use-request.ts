"use client";
import { useEffect, useRef, useState } from "react";
import { notifyFailure } from "./notifications";
import { ApiError, returnToLogin } from "./api/client";
export function useRequest<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{ key: string; data?: T; error?: Error }>();
  const failed = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    load(abort.signal).then(data => { if (!abort.signal.aborted) { failed.current = false; setState({ key, data }); } })
      .catch(error => {
        if (abort.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) { returnToLogin(); return; }
        // Multiple related panels can fail together. Share a toast ID, and keep
        // failed searches/retries quiet until this reader has recovered.
        if (!failed.current) notifyFailure(error, "Could not load data", "admin-read-error");
        failed.current = true;
        setState({ key, error: error instanceof Error ? error : new Error("Request failed") });
      });
    return () => abort.abort();
  }, [key, load]);
  return state?.key === key ? { ...state, loading: false } : { data: undefined, error: undefined, loading: true };
}
