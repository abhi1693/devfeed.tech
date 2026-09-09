"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { useSettings } from "./use-settings";
import { notifyFailure } from "./notifications";
export const refreshIntervals = [0, 5, 10, 15, 30, 60] as const;
export const defaultRefreshSeconds = 10;
const Context = createContext<readonly [number, (seconds: number) => void] | null>(null);

export function RefreshIntervalProvider({ children }: { children: ReactNode }) {
  const { settings, save } = useSettings();
  const preferred = settings.defaults.refresh_seconds;
  const [current, setCurrent] = useState<{ preferred: number; seconds: number }>({ preferred, seconds: preferred });
  if (current.preferred !== preferred) setCurrent({ preferred, seconds: preferred });
  function change(seconds: number) {
    if (!refreshIntervals.includes(seconds as typeof preferred)) return;
    setCurrent({ preferred, seconds });
    void save("defaults", { ...settings.defaults, refresh_seconds: seconds as typeof preferred }).catch(error => {
      setCurrent({ preferred, seconds: preferred }); notifyFailure(error, "Could not save refresh interval");
    });
  }
  return <Context.Provider value={[current.seconds, change]}>{children}</Context.Provider>;
}
export function useRefreshInterval() {
  const context = useContext(Context);
  const local = useState<number>(defaultRefreshSeconds);
  return context ?? local;
}
