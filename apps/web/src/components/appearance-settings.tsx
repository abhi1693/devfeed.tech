"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { Select } from "@devfeed/ui/select";
import type { ThemeMode } from "@devfeed/ui/theme-toggle";
import { AccountGate } from "./user-account";
import { UserSettingsLayout } from "./user-settings-layout";
import { LoadingSkeleton } from "./loading-skeleton";
import { useThemePreferences } from "./theme-preferences";

export function AppearanceSettings() {
  return <UserSettingsLayout section="appearance"><AccountGate returnTo="/settings/appearance"><AppearanceForm /></AccountGate></UserSettingsLayout>;
}
function AppearanceForm() {
  const { theme, save, loading, busy, unavailable, error, refresh } = useThemePreferences();
  if (loading) return <LoadingSkeleton kind="form" label="Loading appearance settings…" />;
  if (unavailable) return <section className="profile-load-error" role="status"><h2>Couldn’t load appearance settings</h2><button className="settings-button" onClick={refresh}>Retry</button></section>;
  const Icon = { system: Monitor, light: Sun, dark: Moon }[theme];
  return <section className="profile-panel" aria-label="Appearance">
    <p className="profile-description">Choose how DevFeed looks on your devices.</p>
    <div className="settings-field appearance-field">
      <label htmlFor="appearance-theme">Theme</label>
      <div>
        <Select id="appearance-theme" label="Theme" required value={theme} disabled={busy} icon={<Icon size={16} aria-hidden="true" />}
          options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]}
          onChange={value => { void save(value as ThemeMode); }} />
        <p className="appearance-help">System follows your device’s appearance. Changes save automatically.</p>
      </div>
    </div>
    {error && <p className="profile-feedback error" role="alert">{error}</p>}
    {busy && <p className="profile-feedback" role="status">Saving theme…</p>}
  </section>;
}
