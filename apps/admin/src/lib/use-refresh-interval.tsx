"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

export const refreshIntervals = [0, 5, 10, 15, 30, 60] as const;
export const defaultRefreshSeconds = 10;
const Context = createContext<readonly [number, (seconds: number) => void] | null>(null);

/** Keep the selected interval while navigating between admin pages. */
export function RefreshIntervalProvider({ children }: { children: ReactNode }) {
  const state = useState<number>(defaultRefreshSeconds);
  return <Context.Provider value={state}>{children}</Context.Provider>;
}

export function useRefreshInterval() {
  const context = useContext(Context);
  const local = useState<number>(defaultRefreshSeconds);
  return context ?? local;
}
