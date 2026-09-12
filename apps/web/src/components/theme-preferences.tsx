"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { defaultDateTimePreferences, type DateTimePreferences } from "@devfeed/ui/date-format";
import type { ThemeMode } from "@devfeed/ui/theme-toggle";
import { setBrowserTheme, useBrowserTheme } from "@/lib/browser-theme";
import { userRequest } from "@/lib/user";
import { useUser } from "./user-account";

export type Appearance = DateTimePreferences & { theme: ThemeMode };
const defaults: Appearance = { ...defaultDateTimePreferences, theme: "system" };
const Context = createContext({
  appearance: defaults,
  loading: false,
  busy: false,
  unavailable: false,
  error: "",
  refresh: () => {},
  update: async (patch: Partial<Appearance>) => {
    if (patch.theme) setBrowserTheme(patch.theme);
    return true;
  },
  save: async (theme: ThemeMode) => {
    setBrowserTheme(theme);
    return true;
  },
});
export function useDateTimePreferences() {
  return useContext(Context).appearance;
}
export function useThemePreferences() {
  const state = useContext(Context);
  const theme = useBrowserTheme();
  return { ...state, theme, appearance: { ...state.appearance, theme } };
}
export function ThemePreferencesProvider({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<{
    owner: string;
    unavailable: boolean;
    appearance: Appearance;
  } | null>(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ owner: string; message: string } | null>(null);
  const account = useRef<AbortController | null>(null);
  useEffect(() => {
    if (loading || !user) return;
    const controller = new AbortController();
    account.current = controller;
    userRequest<Appearance>("settings/appearance", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        setBrowserTheme(value.theme);
        setState({ owner, unavailable: false, appearance: { ...defaults, ...value } });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setState({ owner, unavailable: true, appearance: defaults });
      });
    return () => controller.abort();
  }, [owner, user, loading, revision]);
  const current = state?.owner === owner ? state : null;
  const pending = loading || (!!user && !current);
  async function update(patch: Partial<Appearance>) {
    if (pending || saving || current?.unavailable) return false;
    if (!user) {
      if (patch.theme) setBrowserTheme(patch.theme);
      return true;
    }
    const controller = account.current;
    if (!controller || controller.signal.aborted) return false;
    setSaving(owner);
    setFailure(null);
    try {
      const value = await userRequest<Appearance>("settings/appearance", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify({ ...current?.appearance, ...patch }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return false;
      setBrowserTheme(value.theme);
      setState({ owner, unavailable: false, appearance: { ...defaults, ...value } });
      return true;
    } catch {
      if (!controller.signal.aborted)
        setFailure({ owner, message: "Couldn’t save your appearance settings. Please try again." });
      return false;
    } finally {
      setSaving(null);
    }
  }
  return (
    <Context.Provider
      value={{
        appearance: current?.appearance ?? defaults,
        loading: pending,
        busy: saving === owner,
        unavailable: !!user && !!current?.unavailable,
        error: failure?.owner === owner ? failure.message : "",
        update,
        save: (theme: ThemeMode) => update({ theme }),
        refresh: () => setRevision((value) => value + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}
