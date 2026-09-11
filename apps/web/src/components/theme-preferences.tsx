"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ThemeMode } from "@devfeed/ui/theme-toggle";
import { setBrowserTheme, useBrowserTheme } from "@/lib/browser-theme";
import { userRequest } from "@/lib/user";
import { useUser } from "./user-account";

type Appearance = { theme: ThemeMode };
const Context = createContext({
  loading: false, busy: false, unavailable: false, error: "", refresh: () => {},
  save: async (theme: ThemeMode) => { setBrowserTheme(theme); return true; },
});
export function useThemePreferences() {
  const state = useContext(Context);
  const theme = useBrowserTheme();
  return { ...state, theme };
}
export function ThemePreferencesProvider({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<{ owner: string; unavailable: boolean } | null>(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ owner: string; message: string } | null>(null);
  const account = useRef<AbortController | null>(null);
  useEffect(() => {
    if (loading || !user) return;
    const controller = new AbortController();
    account.current = controller;
    userRequest<Appearance>("settings/appearance", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })
      .then(value => {
        if (controller.signal.aborted) return;
        setBrowserTheme(value.theme);
        setState({ owner, unavailable: false });
      }).catch(() => {
        if (!controller.signal.aborted) setState({ owner, unavailable: true });
      });
    return () => controller.abort();
  }, [owner, user, loading, revision]);
  const current = state?.owner === owner ? state : null;
  const pending = loading || (!!user && !current);
  async function save(theme: ThemeMode) {
    if (pending || saving || current?.unavailable) return false;
    if (!user) { setBrowserTheme(theme); return true; }
    const controller = account.current;
    if (!controller || controller.signal.aborted) return false;
    setSaving(owner); setFailure(null);
    try {
      const value = await userRequest<Appearance>("settings/appearance", {
        method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify({ theme }), signal: controller.signal,
      });
      if (controller.signal.aborted) return false;
      setBrowserTheme(value.theme);
      return true;
    } catch {
      if (!controller.signal.aborted) setFailure({ owner, message: "Couldn’t save your theme. Please try again." });
      return false;
    } finally { setSaving(null); }
  }
  return <Context.Provider value={{ loading: pending, busy: saving === owner, unavailable: !!user && !!current?.unavailable, error: failure?.owner === owner ? failure.message : "", save, refresh: () => setRevision(value => value + 1) }}>{children}</Context.Provider>;
}
