"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { notifyFailure } from "./notifications";
import { ApiError, returnToLogin } from "./api/client";
import { usePolling } from "./use-polling";
import { runWhenPageActive } from "@devfeed/ui/page-activity";

export function useRequest<T>(key: string, load: (signal: AbortSignal) => Promise<T>, pollInterval = 0) {
  const [state, setState] = useState<{ key: string; data?: T; error?: Error; refreshing?: boolean }>();
  const failed = useRef(false);
  const unauthorized = useRef(false);
  const pending = useRef<AbortSignal | null>(null);
  const refresh = useCallback(async (signal: AbortSignal, automatic = false) => {
    if (automatic && (unauthorized.current || (pending.current && !pending.current.aborted))) return;
    pending.current = signal;
    if (automatic) setState(previous => previous?.key === key ? { ...previous, refreshing: true } : previous);
    try {
      const data = await load(signal);
      if (!signal.aborted) { failed.current = false; setState({ key, data }); }
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) { unauthorized.current = true; returnToLogin(); return; }
      // Related panels share an error toast; retries stay quiet until recovery.
      if (!failed.current) notifyFailure(error, "Could not load data", "admin-read-error");
      failed.current = true;
      setState(previous => ({ key, data: previous?.key === key ? previous.data : undefined, error: error instanceof Error ? error : new Error("Request failed") }));
    } finally {
      if (pending.current === signal) {
        pending.current = null;
        setState(previous => previous?.key === key && previous.refreshing ? { ...previous, refreshing: false } : previous);
      }
    }
  }, [key, load]);
  useEffect(() => {
    unauthorized.current = false;
    let complete = false;
    return runWhenPageActive(signal => {
      if (complete) return;
      void refresh(signal).then(() => { if (!signal.aborted) complete = true; });
    });
  }, [refresh]);
  usePolling(signal => refresh(signal, true), pollInterval, key);
  return state?.key === key
    ? { ...state, loading: false, refreshing: !!state.refreshing && pollInterval > 0 }
    : { data: undefined, error: undefined, loading: true, refreshing: false };
}
