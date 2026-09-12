"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { Select, type SelectOption } from "@devfeed/ui/select";
import { formatDate, timezoneOptions } from "@devfeed/ui/date-format";
import { AccountGate } from "./user-account";
import { UserSettingsLayout } from "./user-settings-layout";
import { LoadingSkeleton } from "./loading-skeleton";
import { useThemePreferences, type Appearance } from "./theme-preferences";

export function AppearanceSettings() {
  return (
    <UserSettingsLayout section="appearance">
      <AccountGate returnTo="/settings/appearance">
        <AppearanceForm />
      </AccountGate>
    </UserSettingsLayout>
  );
}
function AppearanceForm() {
  const { appearance, update, loading, busy, unavailable, error, refresh } = useThemePreferences();
  if (loading) return <LoadingSkeleton kind="form" label="Loading appearance settings…" />;
  if (unavailable)
    return (
      <section className="profile-load-error" role="status">
        <h2>Couldn’t load appearance settings</h2>
        <button className="settings-button" onClick={refresh}>
          Retry
        </button>
      </section>
    );
  const Icon = { system: Monitor, light: Sun, dark: Moon }[appearance.theme];
  const choice = (
    key: keyof Appearance,
    label: string,
    options: SelectOption[],
    search = false,
  ) => (
    <div className="settings-field appearance-field">
      <label htmlFor={`appearance-${key}`}>{label}</label>
      <Select
        id={`appearance-${key}`}
        label={label}
        required
        value={appearance[key]}
        disabled={busy}
        icon={key === "theme" ? <Icon size={16} aria-hidden="true" /> : undefined}
        options={options}
        search={search ? {} : undefined}
        onChange={(value) => {
          void update({ [key]: value });
        }}
      />
    </div>
  );
  return (
    <section className="profile-panel appearance-settings" aria-label="Appearance">
      <p className="profile-description">Choose how DevFeed looks on your devices.</p>
      {choice("theme", "Theme", [
        { value: "system", label: "System" },
        { value: "light", label: "Light" },
        { value: "dark", label: "Dark" },
      ])}
      <div className="appearance-date-settings">
        {choice("timezone", "Timezone", timezoneOptions(appearance.timezone), true)}
        {choice("date_format", "Date format", [
          { value: "locale", label: "Device format" },
          { value: "iso", label: "YYYY-MM-DD" },
          { value: "day-first", label: "DD/MM/YYYY" },
          { value: "month-first", label: "MM/DD/YYYY" },
        ])}
        {choice("time_format", "Time format", [
          { value: "system", label: "Device format" },
          { value: "12", label: "12 hour" },
          { value: "24", label: "24 hour" },
        ])}
        <p className="appearance-date-preview">
          Date preview:{" "}
          <span suppressHydrationWarning>{formatDate("2026-09-10T14:30:00Z", appearance)}</span>
        </p>
      </div>
      <p className="appearance-save-status" role="status">
        {busy ? "Saving…" : "Changes save automatically."}
      </p>
      {error && (
        <p className="profile-feedback error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
