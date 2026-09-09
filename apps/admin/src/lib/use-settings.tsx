"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { adminSettingsGet, adminSettingsProfile, adminSettingsAppearance, adminSettingsDefaults, adminSettingsNotifications, adminSettingsTable, adminSettingsTablesReset } from "./api/generated/admin";
import type { AdminIdentity, UserSettings, TableSettingsPatch } from "./api/generated/models";
import { defaultSettings, normalizeSettings, type Settings, type SettingsSection } from "./settings";

type ContextValue = {
  settings: Settings;
  persistent: boolean;
  save: <S extends SettingsSection>(section: S, value: Settings[S]) => Promise<void>;
  saveTable: (key: string, value: TableSettingsPatch) => Promise<void>;
  resetTables: () => Promise<void>;
};
const Context = createContext<ContextValue>({ settings: defaultSettings, persistent: false, save: async () => {}, saveTable: async () => {}, resetTables: async () => {} });

export function SettingsProvider({ admin, initial, children }: { admin: AdminIdentity; initial?: UserSettings; children: ReactNode }) {
  const [settings, setSettings] = useState(() => normalizeSettings(initial ?? {}));
  const queue = useRef(Promise.resolve());
  const revision = useRef(0);
  const mounted = useRef(true);
  const persistent = initial !== undefined;
  const schedule = useCallback((operation: () => Promise<void>) => {
    revision.current++;
    const next = queue.current.then(operation);
    queue.current = next.catch(() => {});
    return next;
  }, []);
  const save = useCallback(async <S extends SettingsSection>(section: S, value: Settings[S]) => {
    if (!persistent) { setSettings(previous => ({ ...previous, [section]: value })); return; }
    return schedule(async () => {
      const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
      const actions = { profile: adminSettingsProfile, notifications: adminSettingsNotifications, appearance: adminSettingsAppearance, defaults: adminSettingsDefaults };
      const action = actions[section] as (body: Settings[S], options: { headers: Record<string, string> }) => Promise<UserSettings>;
      const result = normalizeSettings(await action(value, options));
      if (mounted.current) setSettings(previous => ({ ...previous, [section]: result[section] }));
    });
  }, [persistent, admin.csrf_token, schedule]);
  const saveTable = useCallback(async (key: string, value: TableSettingsPatch) => {
    if (!persistent) return;
    return schedule(async () => {
      const result = normalizeSettings(await adminSettingsTable(key, value, { headers: { "X-CSRF-Token": admin.csrf_token } }));
      if (mounted.current) setSettings(previous => ({ ...previous, tables: { ...previous.tables, [key]: result.tables[key] } }));
    });
  }, [persistent, admin.csrf_token, schedule]);
  const resetTables = useCallback(async () => {
    return schedule(async () => {
      if (persistent) await adminSettingsTablesReset({ headers: { "X-CSRF-Token": admin.csrf_token } });
      if (mounted.current) setSettings(previous => ({ ...previous, tables: {} }));
    });
  }, [persistent, admin.csrf_token, schedule]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const refresh = async () => {
      const before = revision.current;
      await queue.current;
      try {
        const result = await adminSettingsGet({ signal: controller.signal });
        if (!controller.signal.aborted && revision.current === before) setSettings(normalizeSettings(result));
      } catch { /* Keep the last saved preferences through temporary outages. */ }
    };
    if (persistent) window.addEventListener("focus", refresh);
    return () => { mounted.current = false; controller.abort(); window.removeEventListener("focus", refresh); };
  }, [persistent]);
  useEffect(() => {
    const root = document.documentElement;
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const apply = () => root.classList.toggle("dark", settings.appearance.theme === "dark" || (settings.appearance.theme === "system" && !!media?.matches));
    apply(); media?.addEventListener("change", apply);
    root.dataset.density = settings.appearance.density;
    root.dataset.reduceMotion = String(settings.appearance.reduce_motion);
    return () => { media?.removeEventListener("change", apply); root.classList.remove("dark"); delete root.dataset.density; delete root.dataset.reduceMotion; };
  }, [settings.appearance]);
  return <Context.Provider value={{ settings, persistent, save, saveTable, resetTables }}>{children}</Context.Provider>;
}

export function useSettings() { return useContext(Context); }
